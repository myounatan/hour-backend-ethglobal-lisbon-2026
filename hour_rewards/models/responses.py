import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

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
