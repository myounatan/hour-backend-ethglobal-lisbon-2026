from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

from hour_rewards.base import TimestampedModel

if TYPE_CHECKING:
    from hour_rewards.host_models import Venue


class RewardProgramBase(SQLModel):
    """Base reward program model with common fields."""

    punches_required: int = Field(default=10, ge=1)
    reward_description: str = Field(max_length=256)

    is_enabled: bool = Field(default=True)


class RewardProgramCreate(RewardProgramBase):
    """Model for opting a venue into rewards."""

    venue_id: UUID


class RewardProgram(TimestampedModel, RewardProgramBase, table=True):
    """A venue's punch-card rewards config.

    One row per venue, and the row's existence *is* the venue's opt-in.
    ``is_enabled=False`` pauses the program without discarding the threshold,
    reward copy, or any punch/redemption history tied to it.

    ``punches_required`` and ``reward_description`` are read live when evaluating a
    card, so changing them applies to in-progress cards immediately. Redemptions
    snapshot both values (see ``RewardRedemption``) so past rewards stay accurate.
    """

    __tablename__ = "reward_programs"
    __table_args__ = (UniqueConstraint("venue_id", name="uq_reward_programs_venue_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    venue_id: UUID = Field(foreign_key="venues.id", ondelete="CASCADE")

    # Hedera (optional; null until the ledger layer is configured -- see hour_rewards.hedera).
    # The venue's own HTS NFT collection, one serial per user's card, and the HCS topic its
    # punches and redemptions are logged to.
    hedera_token_id: Optional[str] = Field(default=None, max_length=64)
    hedera_topic_id: Optional[str] = Field(default=None, max_length=64)

    # Relationships
    venue: "Venue" = Relationship(back_populates="reward_program")


class RewardProgramUpdateRequest(BaseModel):
    """Model for updating a venue's reward program."""

    punches_required: Optional[int] = None
    reward_description: Optional[str] = None
    is_enabled: Optional[bool] = None


class RewardProgramResponse(BaseModel):
    """API response model for a venue's reward program."""

    id: UUID
    venue_id: UUID
    punches_required: int
    reward_description: str
    is_enabled: bool
    hedera_token_id: Optional[str] = None
    hedera_topic_id: Optional[str] = None

    class Config:
        json_encoders = {UUID: str}
