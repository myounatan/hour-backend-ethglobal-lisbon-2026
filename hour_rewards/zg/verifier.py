"""Judging a receipt on 0G Compute, and keeping the proof of who judged it.

The call goes through a **0G Compute** endpoint, which is OpenAI-compatible: an ordinary chat
completion in JSON mode. What makes it different from a call to a centralized API is what
comes back around it, in two response headers the gateway exposes on purpose:

* ``Provider`` -- the on-chain address of the provider that served the request.
* ``ZG-Res-Key`` -- 0G's key for this response (it also tails the completion id).

That key is the whole attestation story, because ``GET {base_url}/signature/{key}`` answers
publicly, with no API key: an ECDSA signature over ``sha256(request):sha256(response):
provider_type:provider_identity:tls_cert_fingerprint``, plus the ``signing_address`` that signed
it. So the key and provider are what get kept on the punch event and published to the venue's
Hedera topic -- anyone reading that topic can fetch the signature themselves and check that this
punch came from that exact inference.

With ``verify_tee`` on, ``zg_tee_verified`` is settled here, by joining that receipt to the
node's TDX quote at ``GET {node}/v1/quote``: the quote's ``report_data`` is the signing address,
so a match means the key that signed this response lives inside an attested enclave. What is
*not* checked is the quote itself against Intel's PCS, or the ECDSA recovery -- both are public
data anyone can redo from the response key, and neither belongs in a punch-card package.

Note what ``provider_type`` in that receipt does and doesn't mean: ``"centralized"`` says the
model weights run on a hosted API behind the node (which is why the node also publishes the TLS
fingerprint it saw), *not* that there is no TEE. The quote is what answers that.

Nothing here raises at the caller: a Router that is unreachable, unconfigured, or answering
with something other than the JSON it was asked for comes back ``REJECTED`` as
``verifier_unavailable``. An unchecked receipt is never silently a punch -- and because that
verdict is the one refusal :meth:`hour_rewards.RewardService.submit_receipt` doesn't file, an
outage costs the user a retry rather than the receipt.

``openai`` is imported lazily, so a host that installs this package without the ``zg`` extra
can still use the punch-card tables.
"""

import base64
import binascii
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from hour_rewards.models.punch_event import PunchEventStatus
from hour_rewards.zg.config import ZGConfig, get_zg_config
from hour_rewards.zg.prompt import SYSTEM_PROMPT, build_user_message
from hour_rewards.zg.receipt import (
    MODEL_REJECTION_REASONS,
    VERIFIER_UNAVAILABLE,
    ReceiptVerdict,
    decide_status,
    parse_receipt_date,
    parse_total,
    receipt_dedupe_hash,
    venue_name_in_text,
)

logger = logging.getLogger("hour_rewards.zg")

# Models occasionally wrap their JSON in a markdown fence despite being asked not to.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

MAX_NOTES_LENGTH = 2000

# The completion id is the response key with this in front of it.
_RESPONSE_ID_PREFIX = "chatcmpl-"

# `base_url` points at the node's proxy; its quote is served a level up, beside it.
_PROXY_SUFFIX = "/proxy"
_QUOTE_PATH = "/quote"

# One enclave signer per node, and the quote it comes from is ~45KB, so it is fetched once
# rather than per receipt. A node that rotates its key mid-process keeps the old answer until
# restart -- worth knowing, and not worth a cache invalidation scheme for a flag.
_enclave_signers: Dict[str, Optional[str]] = {}


async def verify_receipt(
    venue_id: UUID,
    venue_name: str,
    receipt_text: str,
    *,
    venue_address: Optional[str] = None,
) -> ReceiptVerdict:
    """Read one receipt's OCR text and decide whether it earns a punch at this venue."""
    config = get_zg_config()
    if config is None:
        return _unavailable(
            venue_id, receipt_text, notes="0G verification is not configured on this host."
        )

    try:
        payload, trace = await ask_router(config, venue_name, receipt_text, venue_address)
    except Exception as error:
        logger.warning("0G receipt verification failed for venue %s: %s", venue_id, error)
        return _unavailable(venue_id, receipt_text, notes=f"0G Router call failed: {error}")

    return build_verdict(
        payload,
        venue_id=venue_id,
        venue_name=venue_name,
        receipt_text=receipt_text,
        min_confidence=config.min_confidence,
        trace=trace,
    )


def build_verdict(
    payload: Dict[str, Any],
    *,
    venue_id: UUID,
    venue_name: str,
    receipt_text: str,
    min_confidence: float,
    trace: Optional[Dict[str, Any]] = None,
) -> ReceiptVerdict:
    """Turn the model's JSON into a filing decision, guards and all.

    The venue-name guard is settled here rather than taken on trust: the model's
    ``venue_name_found`` only counts if the name really is somewhere in the text (see
    :func:`hour_rewards.zg.receipt.venue_name_in_text`), which is what stops a hallucinated
    pass from becoming a punch at a venue the user never visited.
    """
    trace = trace or {}
    reason = payload.get("rejection_reason")
    reason = reason if reason in MODEL_REJECTION_REASONS else None

    is_receipt = bool(payload.get("is_receipt"))
    confidence = _clamp_confidence(payload.get("confidence"))
    receipt_date = parse_receipt_date(payload.get("receipt_date"))
    receipt_total = parse_total(payload.get("receipt_total"))
    receipt_identifier = _clean_identifier(payload.get("receipt_identifier"))

    venue_name_found = bool(payload.get("venue_name_found")) and venue_name_in_text(
        venue_name, receipt_text
    )
    status, rejection_reason = decide_status(
        is_receipt=is_receipt,
        venue_name_found=venue_name_found,
        confidence=confidence,
        min_confidence=min_confidence,
        rejection_reason=reason,
    )

    return ReceiptVerdict(
        status=status,
        rejection_reason=rejection_reason,
        is_receipt=is_receipt,
        venue_name_found=venue_name_found,
        receipt_date=receipt_date,
        receipt_total=receipt_total,
        receipt_identifier=receipt_identifier,
        confidence=confidence,
        notes=_clean_notes(payload.get("notes")),
        dedupe_hash=receipt_dedupe_hash(
            venue_id,
            receipt_identifier=receipt_identifier,
            receipt_date=receipt_date,
            receipt_total=receipt_total,
            receipt_text=receipt_text,
        ),
        zg_request_id=_trace_value(trace, "request_id"),
        zg_provider_address=_trace_value(trace, "provider"),
        zg_tee_verified=_trace_flag(trace, "tee_verified"),
    )


async def ask_router(
    config: ZGConfig,
    venue_name: str,
    receipt_text: str,
    venue_address: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """One JSON-mode chat completion, returned as ``(parsed answer, 0G trace)``.

    The whole network boundary of this layer, and so what a test stands in for -- the same
    role :mod:`hour_rewards.hedera.transactions` plays for the ledger. Sent through
    ``with_raw_response`` because the headers are half of what is worth keeping.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=config.timeout_seconds
    )
    raw = await client.chat.completions.with_raw_response.create(
        model=config.model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_message(
                    venue_name, receipt_text, venue_address=venue_address
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    response = raw.parse()
    content = response.choices[0].message.content or ""

    trace = _trace(raw.headers, response)
    request_id = trace.get("request_id")
    if config.verify_tee and request_id:
        trace.update(await _attestation(config, str(request_id)))
    return json.loads(_JSON_FENCE.sub("", content)), trace


def signature_url(config: ZGConfig, request_id: str) -> str:
    """Where anyone can fetch 0G's signature for one response, no API key required.

    Public on purpose: a host can hand this to a client beside a punch, and it is what a
    reader of the venue's Hedera topic follows to check the punch for themselves.
    """
    return f"{config.base_url.rstrip('/')}/signature/{request_id}"


def quote_url(config: ZGConfig) -> str:
    """Where the node serving ``base_url`` publishes the TDX quote for its enclave."""
    return f"{config.base_url.rstrip('/').removesuffix(_PROXY_SUFFIX)}{_QUOTE_PATH}"


def _trace(headers: Any, response: Any) -> Dict[str, Any]:
    """Which provider answered, and under which 0G response key.

    Both come from response headers, so their absence is not an error -- a plain
    OpenAI-compatible endpoint (or a local stub) has no trace to give, and the punch is simply
    recorded without one.
    """
    request_id = headers.get("zg-res-key") or _response_key(getattr(response, "id", None))
    return {"request_id": request_id, "provider": headers.get("provider")}


async def _attestation(config: ZGConfig, request_id: str) -> Dict[str, Any]:
    """Whether an attested enclave signed this response, as ``{"tee_verified": bool}``.

    Two public GETs, no API key: the signed receipt for this response, and the node's TDX
    quote. The receipt names the key that signed the response; the quote commits to that same
    key in its ``report_data``. Matching them is the claim -- and both are exactly what anyone
    can redo later from the response key published to Hedera.

    Either fetch failing leaves the flag unset rather than false: nothing was learned about the
    run, which is not the same as learning it wasn't attested.
    """
    receipt = await _fetch_json(config, signature_url(config, request_id))
    if not receipt or not receipt.get("signature"):
        return {}
    signer = str(receipt.get("signing_address") or "").lower()
    enclave_signer = await _enclave_signer(config)
    if not signer or enclave_signer is None:
        return {}
    return {"tee_verified": signer == enclave_signer}


async def _enclave_signer(config: ZGConfig) -> Optional[str]:
    """The address a node's TDX quote commits to, which is the key it signs responses with."""
    url = quote_url(config)
    if url not in _enclave_signers:
        quote = await _fetch_json(config, url)
        _enclave_signers[url] = _report_data_signer((quote or {}).get("report_data"))
    return _enclave_signers[url]


def _report_data_signer(report_data: Any) -> Optional[str]:
    """The address held in a quote's base64 ``report_data``, null-padded to its 64 bytes."""
    if not isinstance(report_data, str) or not report_data:
        return None
    try:
        decoded = base64.b64decode(report_data, validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded.rstrip(b"\x00").decode("ascii", "ignore").strip().lower() or None


async def _fetch_json(config: ZGConfig, url: str) -> Optional[Dict[str, Any]]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            return (await client.get(url)).json()
    except Exception as error:
        logger.warning("0G attestation could not be read from %s: %s", url, error)
        return None


def _response_key(response_id: Optional[str]) -> Optional[str]:
    """The response key as the signature endpoint wants it, taken off the completion id."""
    if not response_id:
        return None
    return response_id.removeprefix(_RESPONSE_ID_PREFIX) or None


def _unavailable(venue_id: UUID, receipt_text: str, *, notes: str) -> ReceiptVerdict:
    """A submission nobody could judge: refused, and hashed on its own text."""
    return ReceiptVerdict(
        status=PunchEventStatus.REJECTED,
        rejection_reason=VERIFIER_UNAVAILABLE,
        notes=notes,
        dedupe_hash=receipt_dedupe_hash(venue_id, receipt_text=receipt_text),
    )


def _trace_value(trace: Dict[str, Any], key: str) -> Optional[str]:
    value = trace.get(key)
    return str(value) if value else None


def _trace_flag(trace: Dict[str, Any], key: str) -> Optional[bool]:
    """``tee_verified`` is absent when verification wasn't asked for, which is not ``False``."""
    value = trace.get(key)
    return bool(value) if isinstance(value, bool) else None


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _clean_identifier(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    # Matches `PunchEvent.receipt_identifier`'s column width.
    return value.strip()[:128]


def _clean_notes(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:MAX_NOTES_LENGTH]
