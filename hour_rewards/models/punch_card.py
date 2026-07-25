from typing import TYPE_CHECKING, List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

from hour_rewards.base import TimestampedModel

# Imported at runtime (not only under TYPE_CHECKING) so the tables the relationships
# below point at are always registered with SQLModel's metadata wherever a punch card
# is loaded -- SQLAlchemy resolves these names lazily at mapper-configuration time.
from hour_rewards.models.punch_event import PunchEvent
from hour_rewards.models.reward_redemption import RewardRedemption
from hour_rewards.models.reward_redemption_code import RewardRedemptionCode

if TYPE_CHECKING:
    from hour_rewards.host_models import User, Venue


class PunchCardBase(SQLModel):
    """Base punch card model with common fields."""

    user_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    venue_id: UUID = Field(foreign_key="venues.id", ondelete="CASCADE", index=True)


class PunchCard(TimestampedModel, PunchCardBase, table=True):
    """One user's punch card at one venue, created lazily on their first punch there.

    A card is never replaced or deleted when its reward is claimed: redeeming bumps
    ``cycle_number`` and resets ``punch_count`` to 0, while every ``PunchEvent`` keeps
    the ``cycle_number`` it was earned under. Current progress is therefore
    ``punch_events`` filtered to this card's ``cycle_number``, and ``punch_count`` is a
    denormalized count of the *verified* ones in that cycle, maintained by the service
    layer so reads don't need an aggregate query.
    """

    __tablename__ = "punch_cards"
    __table_args__ = (UniqueConstraint("user_id", "venue_id", name="uq_user_venue_punch_card"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    cycle_number: int = Field(default=1)
    punch_count: int = Field(default=0)

    # Hedera (optional; null until the ledger layer is configured -- see hour_rewards.hedera).
    # The serial minted for this card in its venue's collection. Minted once, on the first
    # verified punch, then carried across every cycle: redeeming updates the NFT's metadata
    # rather than burning and re-minting, mirroring how the card row itself survives a reset.
    hedera_nft_serial: Optional[int] = Field(default=None)
    # The custodial account holding that serial. Written only once the transfer out of the
    # treasury has succeeded, so a serial with no account here is a card that was minted but
    # never handed over -- the one state :func:`hour_rewards.hedera.reconcile` can finish.
    hedera_nft_account_id: Optional[str] = Field(default=None, max_length=64)

    # Relationships
    user: "User" = Relationship(back_populates="punch_cards")
    venue: "Venue" = Relationship(back_populates="punch_cards")
    punch_events: List["PunchEvent"] = Relationship(back_populates="punch_card")
    redemption_codes: List["RewardRedemptionCode"] = Relationship(back_populates="punch_card")
    redemptions: List["RewardRedemption"] = Relationship(back_populates="punch_card")
