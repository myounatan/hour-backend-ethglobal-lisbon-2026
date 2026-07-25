import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from hour_rewards.models.punch_event import PunchEventStatus
from hour_rewards.models.reward_redemption_code import RewardRedemptionCodeStatus


class RewardHistoryEventType(str, enum.Enum):
    PUNCH = "punch"
    REDEEM = "redeem"


class PunchCardSummaryResponse(BaseModel):
    """A user's live progress on one venue's punch card.

    Mirrors ``PunchCardSummary`` in the companion ``hour-rewards-ui`` package field for
    field, so a host's API layer can serialize this directly for the mobile client. The
    ``hedera_*`` fields are null until the card has been minted (see
    :mod:`hour_rewards.hedera`), and are what let a client link a card to its NFT.
    """

    venue_id: UUID
    punches_earned: int
    punches_required: int
    reward_description: str
    hedera_token_id: Optional[str] = None
    hedera_nft_serial: Optional[int] = None
    hedera_explorer_url: Optional[str] = None

    class Config:
        json_encoders = {UUID: str}


class RewardHistoryEventResponse(BaseModel):
    """One entry in a card's timeline -- a punch earned, or a reward claimed.

    Mirrors ``RewardHistoryEvent`` in ``hour-rewards-ui``.
    """

    id: UUID
    type: RewardHistoryEventType
    occurred_at: datetime

    class Config:
        json_encoders = {UUID: str}


class ReceiptSubmissionResponse(BaseModel):
    """What came of photographing a receipt: whether it earned a punch, and who says so.

    ``approved`` is the one field a client needs (it maps onto ``PunchVerificationResult`` in
    ``hour-rewards-ui``); ``reason`` explains a refusal in the vocabulary of
    :mod:`hour_rewards.zg.receipt`, and ``summary`` carries the card's progress *after* the
    submission so a client needn't re-fetch it.

    The ``zg_*`` and ``hedera_*`` fields are what make an approval checkable rather than
    merely asserted: the attested inference that judged the receipt, and where that punch
    landed on the venue's public ledger. Both are null until those layers are configured.
    """

    punch_event_id: Optional[UUID] = None
    status: PunchEventStatus
    approved: bool
    reason: Optional[str] = None
    notes: Optional[str] = None
    confidence: Optional[float] = None
    summary: Optional[PunchCardSummaryResponse] = None

    zg_request_id: Optional[str] = None
    zg_tee_verified: Optional[bool] = None
    hedera_topic_sequence_number: Optional[int] = None
    hedera_consensus_timestamp: Optional[str] = None

    class Config:
        json_encoders = {UUID: str}


class RewardRedemptionCodeResponse(BaseModel):
    """The QR token issued for a completed card, once it is eligible to redeem."""

    id: UUID
    punch_card_id: UUID
    cycle_number: int
    token: str
    status: RewardRedemptionCodeStatus
    expires_at: Optional[datetime] = None

    class Config:
        json_encoders = {UUID: str}


class RewardRedemptionResponse(BaseModel):
    """A completed, honoured redemption -- the permanent history row."""

    id: UUID
    venue_id: UUID
    user_id: Optional[UUID] = None
    punch_card_id: Optional[UUID] = None
    cycle_number: int
    punches_required: int
    reward_description: str
    redeemed_by_owner_id: Optional[UUID] = None
    created_at: datetime

    class Config:
        json_encoders = {UUID: str}
