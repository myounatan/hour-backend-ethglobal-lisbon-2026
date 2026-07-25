"""The HIP-412 metadata a punch-card NFT points at, and the URI stored on the ledger.

HIP-657 caps an NFT's on-chain metadata at 100 bytes, so the bytes minted (and re-minted
on every ``TokenUpdateNftsTransaction``) are a URI into the host's API, and the JSON that
URI serves is built here from the live card. The URI carries the card's cycle and count as
a version so each state change is its own on-chain transaction rather than a silent edit
behind a stable pointer -- wallets that cache metadata still see progress move.
"""

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from hour_rewards.hedera.config import MAX_METADATA_BYTES, HederaConfig
from hour_rewards.models.punch_card import PunchCard
from hour_rewards.models.reward_program import RewardProgram

HIP412_FORMAT = "HIP412@2.0.0"
COLLECTION_CREATOR = "Hour"


class MetadataTooLargeError(ValueError):
    """The metadata URI exceeded the 100 bytes HIP-657 allows on-chain."""


def card_metadata_uri(
    config: HederaConfig, punch_card_id: UUID, cycle_number: int, punch_count: int
) -> bytes:
    """``{metadata_base_url}/{card_id}?v={cycle}-{count}``, as the bytes to mint."""
    uri = f"{config.metadata_base_url}/{punch_card_id}?v={cycle_number}-{punch_count}"
    encoded = uri.encode()
    if len(encoded) > MAX_METADATA_BYTES:
        raise MetadataTooLargeError(
            f"NFT metadata URI is {len(encoded)} bytes, over the {MAX_METADATA_BYTES}-byte "
            f"limit: {uri}. Shorten HEDERA_NFT_METADATA_BASE_URL."
        )
    return encoded


async def _venue_display(session: AsyncSession, venue_id: UUID) -> Dict[str, Any]:
    """Name and image off the host's ``venues`` table (see README, "Host contract")."""
    result = await session.execute(
        text("SELECT name, images FROM venues WHERE id = :venue_id"), {"venue_id": venue_id}
    )
    row = result.first()
    if row is None:
        return {"name": None, "image": None}
    images = row[1] or []
    return {"name": row[0], "image": images[0] if images else None}


async def build_card_metadata(
    session: AsyncSession, punch_card_id: UUID
) -> Optional[Dict[str, Any]]:
    """The HIP-412 document for one card, or ``None`` if the card or program is gone."""
    card = await session.get(PunchCard, punch_card_id)
    if card is None:
        return None
    program_result = await session.execute(
        select(RewardProgram).where(RewardProgram.venue_id == card.venue_id)
    )
    program = program_result.scalar_one_or_none()
    if program is None:
        return None

    venue = await _venue_display(session, card.venue_id)
    venue_name = venue["name"] or "Venue"
    is_complete = card.punch_count >= program.punches_required

    metadata: Dict[str, Any] = {
        "name": f"{venue_name} - Punch Card",
        "creator": COLLECTION_CREATOR,
        "description": (
            f"{card.punch_count} of {program.punches_required} punches towards "
            f"{program.reward_description} at {venue_name}."
        ),
        "format": HIP412_FORMAT,
        "properties": {
            "venue_id": str(card.venue_id),
            "cycle_number": card.cycle_number,
            "punch_count": card.punch_count,
            "punches_required": program.punches_required,
            "reward_description": program.reward_description,
            "status": "ready_to_redeem" if is_complete else "in_progress",
        },
    }
    if venue["image"]:
        metadata["image"] = venue["image"]
        metadata["type"] = "image/jpeg"
    return metadata
