from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Type

from sqlalchemy import Enum as SAEnum
from sqlmodel import TIMESTAMP, Field, SQLModel


def utc_now() -> datetime:
    """Current UTC time, naive, to match the naive TIMESTAMP columns below."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class TimestampedModel(SQLModel):
    """The ``created_at`` / ``updated_at`` pair every rewards table carries."""

    created_at: datetime = Field(sa_type=TIMESTAMP, default_factory=utc_now)
    updated_at: datetime = Field(sa_type=TIMESTAMP, default_factory=utc_now)


def value_enum(enum_class: Type[PyEnum]) -> SAEnum:
    """A VARCHAR column storing an enum's *values* rather than its member names.

    ``values_callable`` is what keeps ``"pending_review"`` (not ``"PENDING_REVIEW"``) in
    the database, so these columns match what :mod:`hour_rewards.migrations` creates
    regardless of how the host app configures SQLModel.
    """
    return SAEnum(
        enum_class,
        native_enum=False,
        values_callable=lambda enum: [member.value for member in enum],
    )
