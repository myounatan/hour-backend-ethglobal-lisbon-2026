"""Unit tests for the 0G layer's pure parts: gating, guards, hashing, verdict mapping.

Nothing here touches a network or a database, so they run with no API key and without the
``zg`` extra installed. The database side (a submission becoming a punch, a duplicate being
refused) is exercised by the host app's ``test_reward_receipts.py``, since it needs the host's
``users``/``venues`` tables -- same split as ``test_hedera.py``.
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hour_rewards.models.punch_event import PunchEventStatus
from hour_rewards.zg import (
    DEFAULT_BASE_URL,
    DEFAULT_MIN_CONFIDENCE,
    NOT_A_RECEIPT,
    RETRYABLE_REJECTION_REASONS,
    VENUE_MISMATCH,
    VERIFIER_UNAVAILABLE,
    ZGConfig,
    build_verdict,
    configure_zg,
    get_zg_config,
    receipt_dedupe_hash,
    venue_name_in_text,
    verify_receipt,
)
from hour_rewards.zg.prompt import SYSTEM_PROMPT, build_user_message
from hour_rewards.zg.receipt import LOW_CONFIDENCE, decide_status
from hour_rewards.zg.verifier import (
    _report_data_signer,
    _response_key,
    _trace,
    quote_url,
    signature_url,
)

VENUE = "Japas 1"
RECEIPT_TEXT = """JAPAS #1
123 Dundas St W, Toronto
07/24/2026 19:12
Sapporo Draft 8.50
Chicken Karaage 12.00
TOTAL 48.60
VISA ****4242
Check A-10428
"""

APPROVED_PAYLOAD = {
    "is_receipt": True,
    "venue_name_found": True,
    "receipt_date": "2026-07-24T19:12:00",
    "receipt_total": 48.60,
    "receipt_identifier": "A-10428",
    "confidence": 0.93,
    "notes": "Header reads JAPAS #1, total 48.60.",
    "rejection_reason": None,
}

RESPONSE_KEY = "b5c28394-fcfd-4b7e-b7e9-667c8218731a"
PROVIDER = "0xa48f01287233509FD694a22Bf840225062E67836"

# Straight off compute-network-6: the enclave's `report_data`, and the address it holds, which
# is also the `signing_address` on every signed receipt that node hands out.
REPORT_DATA = (
    "MHg4M2RmNEI4RWJBN2MwQjNCNzQwMDE5YjhjOWE3N2ZmRjc3RDUwOGNGAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
)
SIGNING_ADDRESS = "0x83df4b8eba7c0b3b740019b8c9a77fff77d508cf"

TRACE = {"request_id": RESPONSE_KEY, "provider": PROVIDER, "tee_verified": True}


@pytest.fixture(autouse=True)
def unconfigured():
    """Leave the layer dormant unless a test configures it, and never leak config."""
    configure_zg(None)
    yield
    configure_zg(None)


def verdict(payload: dict, *, venue: str = VENUE, text: str = RECEIPT_TEXT, trace: dict = TRACE):
    return build_verdict(
        payload,
        venue_id=uuid4(),
        venue_name=venue,
        receipt_text=text,
        min_confidence=DEFAULT_MIN_CONFIDENCE,
        trace=trace,
    )


def test_build_returns_none_without_an_api_key():
    assert ZGConfig.build(None) is None
    assert ZGConfig.build("") is None


def test_build_drops_unset_optionals_and_falls_back_to_the_defaults():
    config = ZGConfig.build("app-sk-test", model=None, base_url=None)
    assert config is not None
    assert config.base_url == DEFAULT_BASE_URL
    assert config.min_confidence == DEFAULT_MIN_CONFIDENCE
    assert config.verify_tee is True

    # A key is issued for one gateway, so a host that has its own passes both.
    elsewhere = ZGConfig.build("app-sk-test", base_url="https://other.example/v1/proxy")
    assert elsewhere is not None
    assert elsewhere.base_url == "https://other.example/v1/proxy"


def test_the_trace_is_read_off_the_0g_response_headers():
    """The two headers the gateway exposes are the whole handle on a run."""
    trace = _trace(
        {"zg-res-key": RESPONSE_KEY, "provider": PROVIDER},
        SimpleNamespace(id=f"chatcmpl-{RESPONSE_KEY}"),
    )
    assert trace == {"request_id": RESPONSE_KEY, "provider": PROVIDER}


def test_the_response_key_falls_back_to_the_completion_id():
    """Same value either way -- the completion id is the key with `chatcmpl-` in front."""
    trace = _trace({}, SimpleNamespace(id=f"chatcmpl-{RESPONSE_KEY}"))
    assert trace["request_id"] == RESPONSE_KEY
    assert trace["provider"] is None
    # An endpoint that says nothing at all leaves the punch without a trace, not broken.
    assert _trace({}, SimpleNamespace(id=None)) == {"request_id": None, "provider": None}
    assert _response_key(None) is None


def test_the_quote_commits_to_the_key_that_signs_responses():
    """The join that makes `zg_tee_verified` mean something, on a real node's report_data.

    Base64 of the signing address, null-padded to the quote's 64 bytes -- if this ever stops
    decoding to an address, the flag silently becomes unknowable rather than wrong.
    """
    assert _report_data_signer(REPORT_DATA) == SIGNING_ADDRESS
    assert _report_data_signer(None) is None
    assert _report_data_signer("not base64 at all !!") is None


def test_the_proof_urls_are_public_and_sit_beside_each_other():
    config = ZGConfig.build("app-sk-test")
    assert config is not None
    assert signature_url(config, RESPONSE_KEY) == (
        f"https://compute-network-6.integratenetwork.work/v1/proxy/signature/{RESPONSE_KEY}"
    )
    # The quote is the node's, a level up from its proxy.
    assert quote_url(config) == "https://compute-network-6.integratenetwork.work/v1/quote"


async def test_verification_is_refused_while_unconfigured():
    """A punch nobody checked is not a punch. The refusal is retryable, not a verdict."""
    assert get_zg_config() is None
    result = await verify_receipt(uuid4(), VENUE, RECEIPT_TEXT)
    assert result.status == PunchEventStatus.REJECTED
    assert result.rejection_reason == VERIFIER_UNAVAILABLE
    assert result.rejection_reason in RETRYABLE_REJECTION_REASONS
    assert result.approved is False
    # Still hashed, so a submission can be told apart from another one.
    assert result.dedupe_hash


def test_venue_name_guard_reads_through_ocr_mangling():
    assert venue_name_in_text("Japas 1", "JAPAS #1\nTOTAL 48.60")
    # Receipts abbreviate: two of three distinctive words is still this venue.
    assert venue_name_in_text("VIP Billiards Bloor", "VIP BILLIARDS\n1055 BLOOR ST W")
    assert not venue_name_in_text("Japas 1", "STARBUCKS COFFEE\nTOTAL 6.20")


def test_venue_name_guard_is_not_satisfied_by_a_street_name():
    """A real trap: Japas is *on* Bloor St, so one shared word cannot carry a claim."""
    japas_receipt = "Japas\n692 Bloor St. West Toronto, ON\nOrder TOTAL: 113.00"
    assert venue_name_in_text("Japas", japas_receipt)
    assert not venue_name_in_text("VIP Billiards Bloor", japas_receipt)


def test_venue_name_guard_ignores_generic_words_and_stands_aside_without_any():
    # "Bar" alone must not match another venue that merely has a bar in its name.
    assert not venue_name_in_text("Bar Ambar", "SUSHI BAR TOKYO\nTOTAL 20.00")
    # Nothing distinctive to look for: the model's judgement is all there is.
    assert venue_name_in_text("The Pub", "SOME OTHER PLACE\nTOTAL 9.00")


def test_a_clean_receipt_earns_a_punch_and_keeps_the_attestation():
    result = verdict(APPROVED_PAYLOAD)
    assert result.status == PunchEventStatus.VERIFIED
    assert result.approved is True
    assert result.rejection_reason is None
    assert result.receipt_total == Decimal("48.60")
    assert result.receipt_date == datetime(2026, 7, 24, 19, 12)
    assert result.receipt_identifier == "A-10428"
    assert result.zg_request_id == TRACE["request_id"]
    assert result.zg_provider_address == TRACE["provider"]
    assert result.zg_tee_verified is True


def test_the_venue_guard_overrides_a_pass_the_text_does_not_support():
    """The model claiming the venue's name is there does not make it there."""
    result = verdict(APPROVED_PAYLOAD, text="STARBUCKS COFFEE\n07/24/2026\nTOTAL 6.20")
    assert result.status == PunchEventStatus.REJECTED
    assert result.rejection_reason == VENUE_MISMATCH
    assert result.venue_name_found is False


def test_a_refusal_keeps_its_reason_and_a_menu_is_not_a_receipt():
    result = verdict({**APPROVED_PAYLOAD, "is_receipt": False, "rejection_reason": NOT_A_RECEIPT})
    assert (result.status, result.rejection_reason) == (
        PunchEventStatus.REJECTED,
        NOT_A_RECEIPT,
    )
    # An invented reason code is dropped rather than stored.
    made_up = verdict({**APPROVED_PAYLOAD, "is_receipt": False, "rejection_reason": "vibes"})
    assert made_up.rejection_reason == NOT_A_RECEIPT


def test_an_unsure_read_is_refused_rather_than_becoming_a_punch():
    """No middle state: a receipt the model squinted at asks for a better photo."""
    result = verdict({**APPROVED_PAYLOAD, "confidence": 0.4})
    assert result.status == PunchEventStatus.REJECTED
    assert result.rejection_reason == LOW_CONFIDENCE
    assert result.approved is False
    # A real verdict, so unlike an outage it stands: nothing here is retryable.
    assert result.rejection_reason not in RETRYABLE_REJECTION_REASONS


def test_decide_status_checks_guards_before_confidence():
    """A receipt that failed a guard is rejected, however sure the model was of itself."""
    status, reason = decide_status(
        is_receipt=True, venue_name_found=False, confidence=1.0, min_confidence=0.75
    )
    assert (status, reason) == (PunchEventStatus.REJECTED, VENUE_MISMATCH)


def test_a_garbled_answer_never_becomes_a_punch():
    result = verdict({}, trace={})
    assert result.status == PunchEventStatus.REJECTED
    assert result.confidence == 0.0
    # No trace to keep: a plain OpenAI-compatible endpoint has no attestation to give.
    assert result.zg_tee_verified is None


def test_totals_are_read_however_the_model_wrote_them():
    assert verdict({**APPROVED_PAYLOAD, "receipt_total": "$48.60"}).receipt_total == Decimal(
        "48.60"
    )
    assert verdict({**APPROVED_PAYLOAD, "receipt_total": "n/a"}).receipt_total is None


def test_the_same_receipt_hashes_the_same_way_at_one_venue_only():
    venue_id, other_venue_id = uuid4(), uuid4()
    fields = {
        "receipt_identifier": "A-10428",
        "receipt_date": datetime(2026, 7, 24, 19, 12),
        "receipt_total": Decimal("48.60"),
        "receipt_text": RECEIPT_TEXT,
    }
    assert receipt_dedupe_hash(venue_id, **fields) == receipt_dedupe_hash(venue_id, **fields)
    assert receipt_dedupe_hash(venue_id, **fields) != receipt_dedupe_hash(other_venue_id, **fields)


def test_a_receipt_with_no_number_is_identified_by_its_sale():
    venue_id = uuid4()
    sale = {"receipt_date": datetime(2026, 7, 24, 19, 12), "receipt_total": Decimal("48.60")}
    # The same sale written to different precision is still the same sale.
    assert receipt_dedupe_hash(venue_id, **sale) == receipt_dedupe_hash(
        venue_id, receipt_date=sale["receipt_date"], receipt_total=Decimal("48.6")
    )
    later = receipt_dedupe_hash(
        venue_id, receipt_date=datetime(2026, 7, 25, 19, 12), receipt_total=Decimal("48.60")
    )
    assert receipt_dedupe_hash(venue_id, **sale) != later


def test_unreadable_submissions_fall_back_to_their_text_instead_of_colliding():
    """Two rejected photos must not look like duplicates of each other."""
    venue_id = uuid4()
    first = receipt_dedupe_hash(venue_id, receipt_text="blurry one")
    second = receipt_dedupe_hash(venue_id, receipt_text="blurry two")
    assert first != second


def test_the_prompt_states_its_guards_and_the_user_message_carries_the_venue():
    for reason in (NOT_A_RECEIPT, VENUE_MISMATCH):
        assert reason in SYSTEM_PROMPT

    message = build_user_message(VENUE, RECEIPT_TEXT, venue_address="123 Dundas St W")
    assert VENUE in message
    assert "123 Dundas St W" in message
    assert "TOTAL 48.60" in message
