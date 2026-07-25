"""Build public, read-only proof bundles for reward history events."""

import asyncio
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from hour_rewards.hedera.config import HederaConfig, get_hedera_config
from hour_rewards.hedera.mirror import fetch_topic_message
from hour_rewards.models.punch_card import PunchCard
from hour_rewards.models.punch_event import PunchEvent
from hour_rewards.models.responses import (
    HederaProofResponse,
    ReceiptProofResponse,
    RedemptionProofResponse,
    RewardHistoryEventType,
    RewardProofResponse,
    ZgProofResponse,
)
from hour_rewards.models.reward_program import RewardProgram
from hour_rewards.models.reward_redemption import RewardRedemption
from hour_rewards.zg.config import get_zg_config
from hour_rewards.zg.verifier import fetch_attestation


async def build_event_proof(
    session: AsyncSession, user_id: UUID, venue_id: UUID, event_id: UUID
) -> Optional[RewardProofResponse]:
    """Return proof for an event on the caller's card at this venue, or ``None``."""
    card = (
        await session.execute(
            select(PunchCard).where(
                PunchCard.user_id == user_id,
                PunchCard.venue_id == venue_id,
            )
        )
    ).scalar_one_or_none()
    if card is None:
        return None

    program = (
        await session.execute(select(RewardProgram).where(RewardProgram.venue_id == venue_id))
    ).scalar_one_or_none()
    punch = (
        await session.execute(
            select(PunchEvent).where(
                PunchEvent.id == event_id,
                PunchEvent.punch_card_id == card.id,
            )
        )
    ).scalar_one_or_none()
    if punch is not None:
        return await _punch_proof(punch, card, program)

    redemption = (
        await session.execute(
            select(RewardRedemption).where(
                RewardRedemption.id == event_id,
                RewardRedemption.punch_card_id == card.id,
            )
        )
    ).scalar_one_or_none()
    if redemption is None:
        return None
    return await _redemption_proof(redemption, card, program)


async def _punch_proof(
    punch: PunchEvent, card: PunchCard, program: Optional[RewardProgram]
) -> RewardProofResponse:
    hedera, zg = await _live_proofs(punch, card, program)
    return RewardProofResponse(
        id=punch.id,
        type=RewardHistoryEventType.PUNCH,
        occurred_at=punch.created_at,
        cycle_number=punch.cycle_number,
        hedera=hedera,
        zg=zg,
        receipt=ReceiptProofResponse(
            dedupe_hash=punch.dedupe_hash,
            receipt_identifier=punch.receipt_identifier,
            receipt_date=punch.receipt_date,
            receipt_total_amount=_float_or_none(punch.receipt_total_amount),
            ai_confidence_score=_float_or_none(punch.ai_confidence_score),
            status=punch.status,
        ),
    )


async def _redemption_proof(
    redemption: RewardRedemption, card: PunchCard, program: Optional[RewardProgram]
) -> RewardProofResponse:
    hedera, _ = await _live_proofs(redemption, card, program)
    return RewardProofResponse(
        id=redemption.id,
        type=RewardHistoryEventType.REDEEM,
        occurred_at=redemption.created_at,
        cycle_number=redemption.cycle_number,
        hedera=hedera,
        redemption=RedemptionProofResponse(
            reward_description=redemption.reward_description,
            punches_required=redemption.punches_required,
            cycle_number=redemption.cycle_number,
        ),
    )


async def _live_proofs(
    event: PunchEvent | RewardRedemption,
    card: PunchCard,
    program: Optional[RewardProgram],
) -> tuple[HederaProofResponse, Optional[ZgProofResponse]]:
    hedera_config = get_hedera_config()
    zg_config = get_zg_config()
    topic_id = program.hedera_topic_id if program else None
    sequence_number = event.hedera_topic_sequence_number
    message_task = (
        fetch_topic_message(hedera_config, topic_id, sequence_number)
        if hedera_config and topic_id and sequence_number is not None
        else None
    )
    zg_request_id = event.zg_request_id if isinstance(event, PunchEvent) else None
    attestation_task = (
        fetch_attestation(zg_config, zg_request_id) if zg_config and zg_request_id else None
    )
    message, attestation = await asyncio.gather(
        message_task or _none(),
        attestation_task or _none(),
    )

    hedera = _hedera_response(
        event,
        card,
        program,
        hedera_config,
        message,
        attempted_message=message_task is not None,
    )
    zg = (
        _zg_response(event, attestation, attempted_attestation=attestation_task is not None)
        if isinstance(event, PunchEvent) and zg_request_id
        else None
    )
    return hedera, zg


async def _none() -> None:
    return None


def _hedera_response(
    event: PunchEvent | RewardRedemption,
    card: PunchCard,
    program: Optional[RewardProgram],
    config: Optional[HederaConfig],
    message: Any,
    *,
    attempted_message: bool,
) -> HederaProofResponse:
    topic_id = program.hedera_topic_id if program else None
    token_id = program.hedera_token_id if program else None
    if config is None:
        return HederaProofResponse(
            topic_id=topic_id,
            topic_sequence_number=event.hedera_topic_sequence_number,
            consensus_timestamp=event.hedera_consensus_timestamp,
            metadata_transaction_id=event.hedera_metadata_tx_id,
            token_id=token_id,
            nft_serial=card.hedera_nft_serial,
            nft_account_id=card.hedera_nft_account_id,
        )
    hedera_config = config
    mirror = message if isinstance(message, dict) else None
    return HederaProofResponse(
        network=hedera_config.network,
        topic_id=topic_id,
        topic_sequence_number=event.hedera_topic_sequence_number,
        consensus_timestamp=event.hedera_consensus_timestamp,
        metadata_transaction_id=event.hedera_metadata_tx_id,
        token_id=token_id,
        nft_serial=card.hedera_nft_serial,
        nft_account_id=card.hedera_nft_account_id,
        topic_url=hedera_config.hashscan_topic_url(topic_id) if topic_id else None,
        nft_url=(
            hedera_config.hashscan_nft_url(token_id, card.hedera_nft_serial)
            if token_id and card.hedera_nft_serial is not None
            else None
        ),
        metadata_transaction_url=(
            hedera_config.hashscan_transaction_url(event.hedera_metadata_tx_id)
            if event.hedera_metadata_tx_id
            else None
        ),
        account_url=(
            hedera_config.hashscan_account_url(card.hedera_nft_account_id)
            if card.hedera_nft_account_id
            else None
        ),
        message=mirror.get("message") if mirror else None,
        mirror_node_url=mirror.get("mirror_node_url") if mirror else None,
        message_error=(
            "The HCS message could not be read from the mirror node."
            if attempted_message and mirror is None
            else None
        ),
    )


def _zg_response(
    event: PunchEvent, attestation: object, *, attempted_attestation: bool
) -> ZgProofResponse:
    live = attestation if isinstance(attestation, dict) else {}
    return ZgProofResponse(
        request_id=event.zg_request_id,
        provider_address=event.zg_provider_address,
        tee_verified=event.zg_tee_verified,
        signing_address=live.get("signing_address"),
        enclave_signer=live.get("enclave_signer"),
        tee_verified_live=live.get("tee_verified"),
        signature=live.get("signature"),
        signature_url=live.get("signature_url"),
        error=live.get("error")
        or ("0G attestation could not be re-read." if attempted_attestation and not live else None),
    )


def _float_or_none(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None
