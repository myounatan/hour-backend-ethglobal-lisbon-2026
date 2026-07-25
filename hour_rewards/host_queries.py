"""The only reads this package makes against tables the host application owns.

Kept in one place because it is the one part of the package that assumes anything about the
host's own schema beyond the foreign keys in :mod:`hour_rewards.models` -- see "Host
contract" in the README. Deliberately raw SQL: the host's ``Venue`` class isn't importable
from here, and only its name is ever wanted.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FALLBACK_VENUE_NAME = "Venue"


async def venue_name(
    session: AsyncSession, venue_id: UUID, *, max_length: Optional[int] = None
) -> str:
    """A venue's own name, for naming its NFT collection and for checking its receipts.

    Falls back to ``"Venue"`` rather than raising: a missing name should not be able to fail
    a punch, and both callers degrade sensibly (a generically named collection, and a
    venue-name guard that stands aside -- see
    :func:`hour_rewards.zg.receipt.venue_name_in_text`).
    """
    result = await session.execute(
        text("SELECT name FROM venues WHERE id = :venue_id"), {"venue_id": venue_id}
    )
    row = result.first()
    name = (row[0] if row and row[0] else FALLBACK_VENUE_NAME).strip() or FALLBACK_VENUE_NAME
    return name[:max_length] if max_length else name
