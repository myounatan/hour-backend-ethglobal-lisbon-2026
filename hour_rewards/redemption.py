"""The scan half of a reward: what a QR code says, and why honouring one is refused.

A full card is claimed in person -- the customer shows a code, the venue scans it -- so both
ends of that exchange are this package's business: what the code carries, and whether *this*
venue may honour it. Keeping them here rather than in each host's route handler is the same
split as :mod:`hour_rewards.receipt_photo`, which owns the photo half of a punch.

:meth:`hour_rewards.service.RewardService.redeem_code` is the rule underneath, and it is about
time and state: a code is only good while it is pending, unexpired, and still on the cycle it
was issued for. What this module adds is *place*. A bare token says nothing about where it
belongs, so a code shown at the wrong bar could only be turned away after the fact, by whatever
authorization the host happens to run. Naming the venue in the payload lets the scanning app say
so before it asks, and lets the redemption be checked against the venue that did the scanning
rather than the one the code claims -- two independent chances to catch the same mistake.

Refusals are a verdict, not an exception, exactly as with receipts
(:class:`hour_rewards.models.responses.ReceiptSubmissionResponse`): a code that cannot be
honoured is something to show the person holding the phone, in the vocabulary below, and only a
payload that was never one of ours raises.
"""

from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel

from hour_rewards.base import utc_now
from hour_rewards.models.responses import (
    RedemptionScanResponse,
    RewardRedemptionCodeResponse,
    RewardRedemptionResponse,
)
from hour_rewards.models.reward_redemption import RewardRedemption
from hour_rewards.models.reward_redemption_code import RewardRedemptionCode

# Why a scan didn't hand over a reward. A stable vocabulary, so a host can phrase these for
# staff (and count them) without parsing prose -- the redemption counterpart of the rejection
# reasons in :mod:`hour_rewards.zg.receipt`.
WRONG_VENUE = "wrong_venue"
CODE_NOT_FOUND = "code_not_found"
CODE_EXPIRED = "code_expired"
ALREADY_REDEEMED = "already_redeemed"
STALE_CYCLE = "stale_cycle"
CARD_MISSING = "card_missing"
PROGRAM_MISSING = "program_missing"

REDEMPTION_REFUSAL_REASONS = (
    WRONG_VENUE,
    CODE_NOT_FOUND,
    CODE_EXPIRED,
    ALREADY_REDEEMED,
    STALE_CYCLE,
    CARD_MISSING,
    PROGRAM_MISSING,
)

#: Bumped only when a payload stops being readable by the format below.
REDEMPTION_PAYLOAD_VERSION = 1

_SCHEME = "hour"
_ACTION = "redeem"


class RedemptionPayloadError(ValueError):
    """The scan wasn't one of our codes at all -- not a refusal, so nothing was looked up."""


class RedemptionPayload(BaseModel):
    """What a scanned code says about itself.

    Only ``token`` is load-bearing: the rest lets a scanning app recognise a code from the
    wrong venue, or a stale cycle, without a round trip. Everything here is a *claim* by the
    thing being scanned, so it is worth checking against the database and worth nothing on its
    own -- see :meth:`hour_rewards.service.RewardService.redeem_scanned_code`.

    The optional fields are all ``None`` for a code that arrived as a bare token, which is what
    codes issued before this format look like.
    """

    token: str
    venue_id: Optional[UUID] = None
    punch_card_id: Optional[UUID] = None
    cycle_number: Optional[int] = None


def build_redemption_payload(
    *,
    venue_id: UUID,
    punch_card_id: UUID,
    cycle_number: int,
    token: str,
) -> str:
    """The string to encode in the QR image the customer is shown.

    A URI rather than bare JSON so the same code can one day be opened as a link (deep-linking
    a staff app straight into the scan) without becoming a second format to support.
    """
    query = urlencode(
        {
            "venue": str(venue_id),
            "card": str(punch_card_id),
            "cycle": cycle_number,
            "token": token,
        }
    )
    return f"{_SCHEME}://{_ACTION}/v{REDEMPTION_PAYLOAD_VERSION}?{query}"


def parse_redemption_payload(raw: str) -> RedemptionPayload:
    """Read a scanned string, or say why it could not be read.

    Anything without a scheme is taken to be a bare token, so codes predating
    :func:`build_redemption_payload` still redeem. A future version is refused by name, since
    "update the app" is more use to staff than a generic failure.
    """
    text = (raw or "").strip()
    if not text:
        raise RedemptionPayloadError("Nothing was scanned")

    if "://" not in text:
        return RedemptionPayload(token=text)

    parts = urlsplit(text)
    if parts.scheme != _SCHEME or parts.netloc != _ACTION:
        raise RedemptionPayloadError("That QR code is not an Hour redemption code")

    version = parts.path.strip("/")
    if not version.startswith("v") or not version[1:].isdigit():
        raise RedemptionPayloadError("That redemption code's format is unrecognised")
    if int(version[1:]) > REDEMPTION_PAYLOAD_VERSION:
        raise RedemptionPayloadError(
            "That redemption code was made by a newer version of the app"
        )

    fields = dict(parse_qsl(parts.query))
    token = fields.get("token", "").strip()
    if not token:
        raise RedemptionPayloadError("That redemption code carries no token")

    try:
        return RedemptionPayload(
            token=token,
            venue_id=UUID(fields["venue"]) if fields.get("venue") else None,
            punch_card_id=UUID(fields["card"]) if fields.get("card") else None,
            cycle_number=int(fields["cycle"]) if fields.get("cycle") else None,
        )
    except ValueError as error:
        raise RedemptionPayloadError("That redemption code is malformed") from error


def seconds_until(expires_at: Optional[datetime], *, now: Optional[datetime] = None) -> int:
    """Whole seconds of life left, floored at zero, or ``-1`` for a code that never expires.

    Sent instead of (well, alongside) a timestamp because the columns here are naive UTC: a
    client parsing ``expires_at`` has to guess a zone, and would count down against its own
    clock either way. A duration is unambiguous and skew-proof.
    """
    if expires_at is None:
        return -1
    remaining = (expires_at - (now or utc_now())).total_seconds()
    return max(int(remaining), 0)


def redemption_code_response(
    code: RewardRedemptionCode, *, venue_id: UUID
) -> RewardRedemptionCodeResponse:
    """A freshly issued code as its API response, QR payload included.

    ``venue_id`` is passed in rather than read off the code because the code only knows its
    card; the host resolved the venue to authorize the request in the first place.
    """
    return RewardRedemptionCodeResponse(
        id=code.id,
        venue_id=venue_id,
        punch_card_id=code.punch_card_id,
        cycle_number=code.cycle_number,
        token=code.token,
        status=code.status,
        expires_at=code.expires_at,
        expires_in_seconds=seconds_until(code.expires_at),
        qr_payload=build_redemption_payload(
            venue_id=venue_id,
            punch_card_id=code.punch_card_id,
            cycle_number=code.cycle_number,
            token=code.token,
        ),
    )


def redemption_response(redemption: RewardRedemption) -> RewardRedemptionResponse:
    """A honoured redemption as its API response."""
    return RewardRedemptionResponse(
        id=redemption.id,
        venue_id=redemption.venue_id,
        user_id=redemption.user_id,
        punch_card_id=redemption.punch_card_id,
        cycle_number=redemption.cycle_number,
        punches_required=redemption.punches_required,
        reward_description=redemption.reward_description,
        redeemed_by_owner_id=redemption.redeemed_by_owner_id,
        created_at=redemption.created_at,
    )


def refused_scan(reason: str) -> RedemptionScanResponse:
    """A scan that changed nothing, and the reason from the vocabulary above."""
    return RedemptionScanResponse(approved=False, reason=reason)


def honoured_scan(redemption: RewardRedemption) -> RedemptionScanResponse:
    """A scan that handed over a reward, named so staff know what to give out."""
    return RedemptionScanResponse(
        approved=True,
        reward_description=redemption.reward_description,
        redemption=redemption_response(redemption),
    )
