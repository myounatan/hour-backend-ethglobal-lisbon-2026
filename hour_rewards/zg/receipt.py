"""What a verdict is, and the parts of reaching one that need no network.

Kept apart from :mod:`hour_rewards.zg.verifier` (which owns the Router call) so the rules
that decide whether a receipt earns a punch -- the venue-name guard, the confidence
threshold, the hash that makes a receipt single-use -- are readable and testable on their
own, with no API key in sight.
"""

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel

from hour_rewards.models.punch_event import PunchEventStatus

# Why a submission didn't earn a punch. Sent to the model as its allowed vocabulary and
# stored on `PunchEvent.rejection_reason`, so a host can group refusals without parsing prose.
NOT_A_RECEIPT = "not_a_receipt"
VENUE_MISMATCH = "venue_mismatch"
NO_TOTAL = "no_total"
NO_DATE = "no_date"
ILLEGIBLE = "illegible"
DUPLICATE_RECEIPT = "duplicate_receipt"
LOW_CONFIDENCE = "low_confidence"
VERIFIER_UNAVAILABLE = "verifier_unavailable"

# The reasons the model itself may return; the rest are ours to assign.
MODEL_REJECTION_REASONS = (NOT_A_RECEIPT, VENUE_MISMATCH, NO_TOTAL, NO_DATE, ILLEGIBLE)

# Refusals that say nothing about the receipt, so the receipt keeps its second chance:
# `RewardService.submit_receipt` returns these without filing a row, leaving the dedupe hash
# unclaimed and the same photo submittable once the verifier is back.
RETRYABLE_REJECTION_REASONS = (VERIFIER_UNAVAILABLE,)

# Words too common in venue names to prove anything on their own: "Bar Ambar" must not match
# a receipt from "Sushi Bar", so only the distinctive tokens count towards the guard below.
GENERIC_NAME_TOKENS = frozenset(
    {
        "and",
        "bar",
        "bistro",
        "brewing",
        "cafe",
        "club",
        "co",
        "company",
        "eatery",
        "grill",
        "house",
        "inc",
        "kitchen",
        "llc",
        "lounge",
        "ltd",
        "pub",
        "restaurant",
        "tavern",
        "the",
        # `hour_rewards.host_queries.venue_name`'s fallback, so a venue whose name couldn't be
        # read leaves the guard standing aside instead of failing every receipt.
        "venue",
    }
)

MIN_DISTINCTIVE_TOKEN_LENGTH = 3

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


class ReceiptVerdict(BaseModel):
    """One receipt, read and judged: what it says, whether it counts, and who vouches for it.

    ``status`` is the whole point -- :meth:`hour_rewards.RewardService.submit_receipt` files
    the punch under it, and only ``VERIFIED`` moves a card. The ``zg_*`` fields are the
    Router's trace for the call that decided it, kept so the decision stays attributable
    (and, once the Hedera layer is configured, published alongside the punch).
    """

    status: PunchEventStatus
    rejection_reason: Optional[str] = None

    is_receipt: bool = False
    venue_name_found: bool = False
    receipt_date: Optional[datetime] = None
    receipt_total: Optional[Decimal] = None
    receipt_identifier: Optional[str] = None

    confidence: float = 0.0
    notes: Optional[str] = None
    dedupe_hash: str

    zg_request_id: Optional[str] = None
    zg_provider_address: Optional[str] = None
    zg_tee_verified: Optional[bool] = None

    @property
    def approved(self) -> bool:
        return self.status == PunchEventStatus.VERIFIED


def normalize_for_match(text: str) -> str:
    """Lowercase, with every run of punctuation and whitespace collapsed to one space.

    OCR reads "JAPAS#1" and "Japas  -  1" off the same sign, and either has to match the
    venue name "Japas 1" the host stores.
    """
    return _NON_ALPHANUMERIC.sub(" ", (text or "").lower()).strip()


def distinctive_tokens(venue_name: str) -> Tuple[str, ...]:
    """The parts of a venue's name worth looking for in a receipt."""
    tokens = normalize_for_match(venue_name).split()
    return tuple(
        token
        for token in tokens
        if len(token) >= MIN_DISTINCTIVE_TOKEN_LENGTH and token not in GENERIC_NAME_TOKENS
    )


def venue_name_in_text(venue_name: str, receipt_text: str) -> bool:
    """Whether this receipt names the venue it is being claimed at.

    A cheap, deterministic version of the first thing the model is asked to check, run over
    the same text afterwards: a receipt that never names the venue cannot earn a punch there
    no matter how confident the model sounded. It only ever overrides an approval, never
    rescues a refusal.

    Half of a name's distinctive words have to appear, rounding up. Lenient enough for how
    receipts actually print -- OCR mangles logos and headers abbreviate, so "VIP Billiards
    Bloor" showing up as "VIP BILLIARDS" still passes -- but not so lenient that one word
    carries a whole claim: a receipt from Japas at 692 Bloor St. West would otherwise read as
    a receipt from VIP Billiards Bloor.

    With no distinctive words to look for (a venue actually called "The Pub") the guard stands
    aside and leaves the judgement to the model.
    """
    tokens = distinctive_tokens(venue_name)
    if not tokens:
        return True
    haystack = normalize_for_match(receipt_text)
    found = sum(1 for token in tokens if token in haystack)
    return found >= (len(tokens) + 1) // 2


def decide_status(
    *,
    is_receipt: bool,
    venue_name_found: bool,
    confidence: float,
    min_confidence: float,
    rejection_reason: Optional[str] = None,
) -> Tuple[PunchEventStatus, Optional[str]]:
    """Turn a read receipt into a filing decision, as ``(status, rejection_reason)``.

    Pass or refuse, nothing in between: a receipt that fails a guard is ``REJECTED`` with
    the reason why, one the model wasn't sure enough about is ``REJECTED`` as
    ``low_confidence``, and only a confident pass is ``VERIFIED``. A hesitant read is a
    refusal rather than a queue for a human, so the answer to a bad photo is a better photo
    -- which is also why an unjudgeable submission isn't filed at all (see
    :meth:`hour_rewards.RewardService.submit_receipt`) and can simply be tried again.
    """
    if not is_receipt:
        return PunchEventStatus.REJECTED, rejection_reason or NOT_A_RECEIPT
    if not venue_name_found:
        return PunchEventStatus.REJECTED, VENUE_MISMATCH
    if rejection_reason:
        return PunchEventStatus.REJECTED, rejection_reason
    if confidence < min_confidence:
        return PunchEventStatus.REJECTED, LOW_CONFIDENCE
    return PunchEventStatus.VERIFIED, None


def receipt_dedupe_hash(
    venue_id: UUID,
    *,
    receipt_identifier: Optional[str] = None,
    receipt_date: Optional[datetime] = None,
    receipt_total: Optional[Decimal] = None,
    receipt_text: str = "",
) -> str:
    """The value that makes a receipt single-use at a venue.

    Unique per venue in the database (``uq_venue_receipt_dedupe``), so this decides what
    "the same receipt" means. A transaction identifier is used alone when the receipt prints
    one, since it identifies the sale exactly; otherwise the date and total together stand in
    for it. Neither available -- an unreadable photo, refused -- falls back to the text
    itself, so two rejected submissions don't collide on an empty hash and get reported as
    duplicates of each other.
    """
    if receipt_identifier:
        parts = ["id", normalize_for_match(receipt_identifier)]
    elif receipt_date is not None or receipt_total is not None:
        parts = [
            "sale",
            receipt_date.isoformat(timespec="minutes") if receipt_date else "",
            _normalize_total(receipt_total),
        ]
    else:
        parts = ["text", normalize_for_match(receipt_text)]
    return hashlib.sha256("|".join([str(venue_id), *parts]).encode()).hexdigest()


def parse_total(value: Any) -> Optional[Decimal]:
    """A total as the model wrote it -- ``"48.60"``, ``48.6``, ``"$48.60"`` -- or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text or text in ("-", ".", "-."):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_receipt_date(value: Any) -> Optional[datetime]:
    """An ISO-8601 date or timestamp as the model wrote it, naive UTC to match the column."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)


def _normalize_total(total: Optional[Decimal]) -> str:
    """Two decimal places, so ``48.6`` and ``48.60`` are the same sale."""
    if total is None:
        return ""
    return f"{total:.2f}"
