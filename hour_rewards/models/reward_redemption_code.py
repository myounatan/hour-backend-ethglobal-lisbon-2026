import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, UniqueConstraint

from hour_rewards.base import TimestampedModel, value_enum

if TYPE_CHECKING:
    from hour_rewards.host_models import Owner
    from hour_rewards.models.punch_card import PunchCard


class RewardRedemptionCodeStatus(str, enum.Enum):
    PENDING = "pending"
    REDEEMED = "redeemed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class RewardRedemptionCode(TimestampedModel, table=True):
    """A QR code issued for a full punch card, for a venue owner to scan and redeem.

    ``token`` is the opaque value encoded in the QR image the user is shown; it carries
    no card data, so a scan is only meaningful when this row is still ``PENDING``,
    unexpired, and its ``cycle_number`` still matches the card's -- which is what stops
    a screenshotted code from being reused after the card has reset.

    Every code ever generated is kept (rather than one mutable code column on the card)
    so expired and invalidated codes remain auditable alongside the redeemed ones.
    """

    __tablename__ = "reward_redemption_codes"
    __table_args__ = (UniqueConstraint("token", name="uq_reward_redemption_codes_token"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    punch_card_id: UUID = Field(foreign_key="punch_cards.id", ondelete="CASCADE", index=True)
    cycle_number: int = Field(default=1)

    token: str = Field(max_length=64)

    status: RewardRedemptionCodeStatus = Field(
        default=RewardRedemptionCodeStatus.PENDING,
        sa_type=value_enum(RewardRedemptionCodeStatus),
    )
    expires_at: Optional[datetime] = Field(default=None)

    redeemed_at: Optional[datetime] = Field(default=None)
    redeemed_by_owner_id: Optional[UUID] = Field(
        default=None, foreign_key="owners.id", ondelete="SET NULL"
    )

    # Relationships
    punch_card: "PunchCard" = Relationship(back_populates="redemption_codes")
    redeemed_by_owner: Optional["Owner"] = Relationship()
