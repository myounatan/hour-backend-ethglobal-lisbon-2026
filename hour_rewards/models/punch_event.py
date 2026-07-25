import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, UniqueConstraint

from hour_rewards.base import LedgerProofModel, TimestampedModel, value_enum

if TYPE_CHECKING:
    from hour_rewards.host_models import UserImage
    from hour_rewards.models.punch_card import PunchCard


class PunchEventStatus(str, enum.Enum):
    """A submitted receipt either earned its punch or it didn't; there is no middle state.

    Deliberately two-valued: nothing in this package queues a receipt for a human, so a
    verdict nobody can act on is a rejection the user can respond to by submitting again.
    """

    VERIFIED = "verified"
    REJECTED = "rejected"


class PunchEvent(LedgerProofModel, TimestampedModel, table=True):
    """One receipt a user submitted towards a punch card, verified or not.

    Punches are earned by photographing a venue receipt, not granted by owners: the
    receipt's text is judged on 0G Compute (see :mod:`hour_rewards.zg`), and only a
    ``VERIFIED`` row counts towards ``PunchCard.punch_count``. Refused attempts are kept
    rather than discarded so abuse patterns stay visible, and the ``zg_*`` columns record
    which attested inference decided each one.

    Idempotency rests on ``dedupe_hash`` -- a normalized hash of the extracted receipt
    fields -- which is unique per venue, so the same receipt cannot be redeemed twice
    even if it's submitted from a different account. ``cycle_number`` freezes which of
    the card's cycles this punch belongs to, so history survives every reset.

    The host app owns the image table this points at, so it is responsible for importing
    its ``UserImage`` model somewhere before the first query (see README, "Host contract").

    A verified punch is also published to its venue's HCS topic and reflected in the card
    NFT's metadata; the ``hedera_*`` columns from :class:`LedgerProofModel` record where.
    """

    __tablename__ = "punch_events"
    __table_args__ = (UniqueConstraint("venue_id", "dedupe_hash", name="uq_venue_receipt_dedupe"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    punch_card_id: UUID = Field(foreign_key="punch_cards.id", ondelete="CASCADE", index=True)
    # Denormalized from the card so the dedupe constraint above doesn't need a join.
    venue_id: UUID = Field(foreign_key="venues.id", ondelete="CASCADE")
    cycle_number: int = Field(default=1)

    # Receipt data extracted by the AI pipeline
    receipt_image_id: Optional[UUID] = Field(
        default=None, foreign_key="user_images.id", ondelete="SET NULL"
    )
    receipt_date: Optional[datetime] = Field(default=None)
    receipt_total_amount: Optional[Decimal] = Field(default=None)
    receipt_identifier: Optional[str] = Field(default=None, max_length=128)
    dedupe_hash: str = Field(max_length=128)

    # No default: a row exists because something judged this receipt, either way.
    status: PunchEventStatus = Field(sa_type=value_enum(PunchEventStatus))
    rejection_reason: Optional[str] = Field(default=None, max_length=256)

    # Notes for AI to remember
    ai_notes: Optional[str] = Field(default=None)
    ai_confidence_score: Optional[Decimal] = Field(default=None)

    # Which 0G Compute run judged this receipt (see hour_rewards.zg). `zg_request_id` is 0G's
    # response key, which is what makes the run checkable: the node's signature over it, and the
    # enclave quote holding the key that signed it, are both a public GET away. `zg_tee_verified`
    # is those two agreeing -- null when they couldn't be read, which is not the same as False.
    zg_request_id: Optional[str] = Field(default=None, max_length=128)
    zg_provider_address: Optional[str] = Field(default=None, max_length=128)
    zg_tee_verified: Optional[bool] = Field(default=None)

    # Relationships
    punch_card: "PunchCard" = Relationship(back_populates="punch_events")
    receipt_image: Optional["UserImage"] = Relationship()
