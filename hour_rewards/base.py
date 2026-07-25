from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, Type

from sqlalchemy import Enum as SAEnum
from sqlmodel import TIMESTAMP, Field, SQLModel


def utc_now() -> datetime:
    """Current UTC time, naive, to match the naive TIMESTAMP columns below."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class TimestampedModel(SQLModel):
    """The ``created_at`` / ``updated_at`` pair every rewards table carries."""

    created_at: datetime = Field(sa_type=TIMESTAMP, default_factory=utc_now)
    updated_at: datetime = Field(sa_type=TIMESTAMP, default_factory=utc_now)


class LedgerProofModel(SQLModel):
    """Where a row was mirrored on Hedera, for the rows that are worth proving.

    All three stay null when the Hedera layer isn't configured, or when a submission
    failed -- the ledger mirrors the database, it never gates it. See
    :mod:`hour_rewards.hedera` for what gets published where.
    """

    # Position of this row's message on its venue's HCS punch ledger.
    hedera_topic_sequence_number: Optional[int] = Field(default=None)
    hedera_consensus_timestamp: Optional[str] = Field(default=None, max_length=64)
    # The TokenUpdateNftsTransaction that moved the card NFT to the state this row created.
    hedera_metadata_tx_id: Optional[str] = Field(default=None, max_length=128)


def value_enum(enum_class: Type[PyEnum]) -> SAEnum:
    """A VARCHAR column storing an enum's *values* rather than its member names.

    ``values_callable`` is what keeps ``"verified"`` (not ``"VERIFIED"``) in
    the database, so these columns match what :mod:`hour_rewards.migrations` creates
    regardless of how the host app configures SQLModel.
    """
    return SAEnum(
        enum_class,
        native_enum=False,
        values_callable=lambda enum: [member.value for member in enum],
    )
