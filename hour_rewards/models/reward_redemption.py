from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship

from hour_rewards.base import TimestampedModel

if TYPE_CHECKING:
    from hour_rewards.host_models import Owner
    from hour_rewards.models.punch_card import PunchCard


class RewardRedemption(TimestampedModel, table=True):
    """A completed punch card cycle that a venue owner scanned and honoured.

    ``created_at`` is the redemption time. ``venue_id`` and ``user_id`` are
    denormalized off the card, and ``punches_required`` / ``reward_description`` are
    snapshotted off the venue's program, so venue-level reporting stays accurate after
    the program's threshold or reward copy changes -- and survives the card, user, or
    owner row being removed (those references null out rather than cascade).
    """

    __tablename__ = "reward_redemptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    venue_id: UUID = Field(foreign_key="venues.id", ondelete="CASCADE", index=True)
    user_id: Optional[UUID] = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL", index=True
    )
    punch_card_id: Optional[UUID] = Field(
        default=None, foreign_key="punch_cards.id", ondelete="SET NULL", index=True
    )
    redemption_code_id: Optional[UUID] = Field(
        default=None, foreign_key="reward_redemption_codes.id", ondelete="SET NULL"
    )

    cycle_number: int = Field(default=1)

    # Snapshot of the venue's reward program at redemption time
    punches_required: int
    reward_description: str = Field(max_length=256)

    redeemed_by_owner_id: Optional[UUID] = Field(
        default=None, foreign_key="owners.id", ondelete="SET NULL"
    )

    # Relationships
    punch_card: Optional["PunchCard"] = Relationship(back_populates="redemptions")
    redeemed_by_owner: Optional["Owner"] = Relationship()
