"""Receipt verification on 0G Compute: a punch is earned by an attested model run.

A user photographs a venue receipt, the host OCRs it, and this layer decides whether that
text is a genuine receipt from that venue -- through 0G Compute, where the node serving the
model signs each response from inside a TDX enclave, so the run behind it can be checked
afterwards by anyone. What the model answered, how sure it was, and which provider answered
under which response key are all kept on the punch event, so a card's progress traces back to a
specific, verifiable inference.

Four guards gate a punch (see :mod:`hour_rewards.zg.prompt`), and the venue-name one is
re-checked in Python so a hallucinated pass can't become a punch somewhere the user never was.

A submission passes or is refused with a reason -- there is no review queue. Dormant until a
host calls :func:`configure_zg`; unconfigured or unreachable, nothing earns a punch, but that
one refusal is retryable rather than final. See the README's "0G" section.
"""

from hour_rewards.zg.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MODEL,
    ZGConfig,
    configure_zg,
    get_zg_config,
)
from hour_rewards.zg.prompt import SYSTEM_PROMPT, build_user_message
from hour_rewards.zg.receipt import (
    DUPLICATE_RECEIPT,
    ILLEGIBLE,
    LOW_CONFIDENCE,
    NO_DATE,
    NO_TOTAL,
    NOT_A_RECEIPT,
    RETRYABLE_REJECTION_REASONS,
    VENUE_MISMATCH,
    VERIFIER_UNAVAILABLE,
    ReceiptVerdict,
    decide_status,
    receipt_dedupe_hash,
    venue_name_in_text,
)
from hour_rewards.zg.verifier import build_verdict, quote_url, signature_url, verify_receipt

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MODEL",
    "DUPLICATE_RECEIPT",
    "ILLEGIBLE",
    "LOW_CONFIDENCE",
    "NOT_A_RECEIPT",
    "NO_DATE",
    "NO_TOTAL",
    "RETRYABLE_REJECTION_REASONS",
    "ReceiptVerdict",
    "SYSTEM_PROMPT",
    "VENUE_MISMATCH",
    "VERIFIER_UNAVAILABLE",
    "ZGConfig",
    "build_user_message",
    "build_verdict",
    "configure_zg",
    "decide_status",
    "get_zg_config",
    "quote_url",
    "receipt_dedupe_hash",
    "signature_url",
    "venue_name_in_text",
    "verify_receipt",
]
