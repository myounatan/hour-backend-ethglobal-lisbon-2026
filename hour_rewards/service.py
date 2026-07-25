"""The rules from the README, implemented.

``RewardService`` is a static-async service operating only on an ``AsyncSession`` and
this package's own models -- it never imports the host application, matching the "Host
contract" in the README. Authentication and authorization (confirming the caller is the
venue's owner, resolving the current user, etc.) are the host's job and must happen
*before* calling these methods; the one exception is :meth:`RewardService.redeem_code`,
which needs the code's venue resolved first so the host can run that check -- see
:meth:`RewardService.get_redemption_code_venue_id`.

The three methods that change a card's on-chain story -- opting a venue in, banking a
verified punch, and honouring a code -- also mirror themselves onto Hedera via
:class:`hour_rewards.hedera.HederaLedger`. Those calls are no-ops until a host configures
the ledger, and they never raise: the database result is returned whether or not the
network agreed. See :mod:`hour_rewards.hedera`.
"""

import secrets
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from hour_rewards.base import utc_now
from hour_rewards.hedera.config import get_hedera_config
from hour_rewards.hedera.ledger import HederaLedger
from hour_rewards.models.punch_card import PunchCard
from hour_rewards.models.punch_event import PunchEvent, PunchEventStatus
from hour_rewards.models.responses import (
    PunchCardSummaryResponse,
    RewardHistoryEventResponse,
    RewardHistoryEventType,
)
from hour_rewards.models.reward_program import (
    RewardProgram,
    RewardProgramCreate,
    RewardProgramUpdateRequest,
)
from hour_rewards.models.reward_redemption import RewardRedemption
from hour_rewards.models.reward_redemption_code import (
    RewardRedemptionCode,
    RewardRedemptionCodeStatus,
)

REDEMPTION_TOKEN_BYTES = 32


class RewardServiceError(ValueError):
    """A rule violation: not found, not eligible yet, or in the wrong state."""


class RewardService:
    """Punch-card business logic shared by every host of this package."""

    @staticmethod
    def is_program_active(program: Optional[RewardProgram]) -> bool:
        """Whether a venue counts as "opted into rewards" right now.

        A row's existence is the opt-in (see `RewardProgram`), but `is_enabled=False`
        pauses it without deleting history -- so both must hold. Centralized here so
        hosts surfacing "has rewards" (e.g. a venue list filter) don't reimplement it.
        """
        return program is not None and program.is_enabled

    @staticmethod
    async def get_reward_program_for_venue(
        session: AsyncSession, venue_id: UUID
    ) -> Optional[RewardProgram]:
        result = await session.execute(
            select(RewardProgram).where(RewardProgram.venue_id == venue_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_update_reward_program(
        session: AsyncSession, create_model: RewardProgramCreate
    ) -> RewardProgram:
        """Opt a venue in, or update its config if it's already opted in."""
        program = await RewardService.get_reward_program_for_venue(session, create_model.venue_id)
        if program is None:
            program = RewardProgram(**create_model.model_dump())
            session.add(program)
        else:
            for key, value in create_model.model_dump(exclude={"venue_id"}).items():
                setattr(program, key, value)
            program.updated_at = utc_now()
        await session.commit()
        await session.refresh(program)
        # Opting in is what gives a venue its NFT collection and punch topic.
        return await HederaLedger.ensure_program_ledger(session, program)

    @staticmethod
    async def update_reward_program(
        session: AsyncSession, venue_id: UUID, update_model: RewardProgramUpdateRequest
    ) -> RewardProgram:
        program = await RewardService.get_reward_program_for_venue(session, venue_id)
        if program is None:
            raise RewardServiceError(f"No reward program for venue {venue_id}")
        for key, value in update_model.model_dump(exclude_unset=True).items():
            setattr(program, key, value)
        program.updated_at = utc_now()
        await session.commit()
        await session.refresh(program)
        return program

    @staticmethod
    async def get_or_create_punch_card(
        session: AsyncSession, user_id: UUID, venue_id: UUID
    ) -> PunchCard:
        result = await session.execute(
            select(PunchCard).where(PunchCard.user_id == user_id, PunchCard.venue_id == venue_id)
        )
        card = result.scalar_one_or_none()
        if card is not None:
            return card
        card = PunchCard(user_id=user_id, venue_id=venue_id)
        session.add(card)
        await session.commit()
        await session.refresh(card)
        return card

    @staticmethod
    async def get_punch_card_summary(
        session: AsyncSession, user_id: UUID, venue_id: UUID
    ) -> Optional[PunchCardSummaryResponse]:
        """``None`` when the venue has no enabled reward program (nothing to show)."""
        program = await RewardService.get_reward_program_for_venue(session, venue_id)
        if not RewardService.is_program_active(program):
            return None
        card = await RewardService.get_or_create_punch_card(session, user_id, venue_id)
        config = get_hedera_config()
        explorer_url = None
        if config is not None and program.hedera_token_id and card.hedera_nft_serial is not None:
            explorer_url = config.hashscan_nft_url(program.hedera_token_id, card.hedera_nft_serial)
        return PunchCardSummaryResponse(
            venue_id=venue_id,
            punches_earned=card.punch_count,
            punches_required=program.punches_required,
            reward_description=program.reward_description,
            hedera_token_id=program.hedera_token_id,
            hedera_nft_serial=card.hedera_nft_serial,
            hedera_explorer_url=explorer_url,
        )

    @staticmethod
    async def get_punch_history(
        session: AsyncSession, user_id: UUID, venue_id: UUID
    ) -> List[RewardHistoryEventResponse]:
        """Verified punches and redemptions for this user+venue, newest first."""
        card_result = await session.execute(
            select(PunchCard).where(PunchCard.user_id == user_id, PunchCard.venue_id == venue_id)
        )
        card = card_result.scalar_one_or_none()
        if card is None:
            return []

        punches_result = await session.execute(
            select(PunchEvent).where(
                PunchEvent.punch_card_id == card.id,
                PunchEvent.status == PunchEventStatus.VERIFIED,
            )
        )
        redemptions_result = await session.execute(
            select(RewardRedemption).where(RewardRedemption.punch_card_id == card.id)
        )

        events = [
            RewardHistoryEventResponse(
                id=punch.id, type=RewardHistoryEventType.PUNCH, occurred_at=punch.created_at
            )
            for punch in punches_result.scalars().all()
        ] + [
            RewardHistoryEventResponse(
                id=redemption.id,
                type=RewardHistoryEventType.REDEEM,
                occurred_at=redemption.created_at,
            )
            for redemption in redemptions_result.scalars().all()
        ]
        events.sort(key=lambda e: e.occurred_at, reverse=True)
        return events

    @staticmethod
    async def _verified_punch_count(session: AsyncSession, card: PunchCard) -> int:
        """The card's real progress: its verified punches in the cycle it's on now."""
        result = await session.execute(
            select(func.count())
            .select_from(PunchEvent)
            .where(
                PunchEvent.punch_card_id == card.id,
                PunchEvent.cycle_number == card.cycle_number,
                PunchEvent.status == PunchEventStatus.VERIFIED,
            )
        )
        return int(result.scalar_one())

    @staticmethod
    async def record_verified_punch(
        session: AsyncSession, punch_card_id: UUID, punch_event_id: Optional[UUID] = None
    ) -> PunchCard:
        """Recount a card's punches. Called once a ``PunchEvent`` is marked ``VERIFIED``.

        Recomputed from the ``VERIFIED`` events in the card's current cycle rather than
        incremented, which is what ``punch_count`` is documented to be -- so calling this
        twice for the same punch, as a retried request would, cannot inflate a card.

        Pass the ``PunchEvent`` this punch came from to have its identifier and receipt
        hash published alongside the punch on the venue's Hedera topic, and the resulting
        references written back onto that row.
        """
        card = await session.get(PunchCard, punch_card_id)
        if card is None:
            raise RewardServiceError(f"Punch card {punch_card_id} not found")
        card.punch_count = await RewardService._verified_punch_count(session, card)
        card.updated_at = utc_now()
        await session.commit()
        await session.refresh(card)

        punch_event = (
            await session.get(PunchEvent, punch_event_id) if punch_event_id is not None else None
        )
        return await HederaLedger.record_punch(session, card, punch_event)

    @staticmethod
    async def generate_redemption_code(
        session: AsyncSession, user_id: UUID, venue_id: UUID
    ) -> RewardRedemptionCode:
        """Issue a QR token once a card has reached its program's threshold."""
        program = await RewardService.get_reward_program_for_venue(session, venue_id)
        if program is None:
            raise RewardServiceError(f"Venue {venue_id} has no reward program")
        card = await RewardService.get_or_create_punch_card(session, user_id, venue_id)
        if card.punch_count < program.punches_required:
            raise RewardServiceError("Punch card has not reached the required threshold")

        code = RewardRedemptionCode(
            punch_card_id=card.id,
            cycle_number=card.cycle_number,
            token=secrets.token_urlsafe(REDEMPTION_TOKEN_BYTES),
        )
        session.add(code)
        await session.commit()
        await session.refresh(code)
        return code

    @staticmethod
    async def get_redemption_code_venue_id(session: AsyncSession, token: str) -> Optional[UUID]:
        """Resolve which venue a code belongs to, so the host can authorize the scanning
        owner *before* calling :meth:`redeem_code` -- the token alone doesn't carry it."""
        result = await session.execute(
            select(RewardRedemptionCode).where(RewardRedemptionCode.token == token)
        )
        code = result.scalar_one_or_none()
        if code is None:
            return None
        card = await session.get(PunchCard, code.punch_card_id)
        return card.venue_id if card else None

    @staticmethod
    async def redeem_code(
        session: AsyncSession, token: str, redeemed_by_owner_id: Optional[UUID]
    ) -> RewardRedemption:
        """Validate and honour a code: writes the history row and resets the card's cycle.

        Assumes the caller has already been authorized against the venue resolved by
        :meth:`get_redemption_code_venue_id`.
        """
        result = await session.execute(
            select(RewardRedemptionCode).where(RewardRedemptionCode.token == token)
        )
        code = result.scalar_one_or_none()
        if code is None:
            raise RewardServiceError("Redemption code not found")
        if code.status != RewardRedemptionCodeStatus.PENDING:
            raise RewardServiceError(f"Redemption code is {code.status.value}, not pending")

        now = utc_now()
        if code.expires_at is not None and code.expires_at <= now:
            code.status = RewardRedemptionCodeStatus.EXPIRED
            await session.commit()
            raise RewardServiceError("Redemption code has expired")

        card = await session.get(PunchCard, code.punch_card_id)
        if card is None:
            raise RewardServiceError("Punch card not found for this code")
        if code.cycle_number != card.cycle_number:
            code.status = RewardRedemptionCodeStatus.INVALIDATED
            await session.commit()
            raise RewardServiceError("Redemption code is from a previous cycle")

        program = await RewardService.get_reward_program_for_venue(session, card.venue_id)
        if program is None:
            raise RewardServiceError(f"Venue {card.venue_id} has no reward program")

        redemption = RewardRedemption(
            venue_id=card.venue_id,
            user_id=card.user_id,
            punch_card_id=card.id,
            redemption_code_id=code.id,
            cycle_number=card.cycle_number,
            punches_required=program.punches_required,
            reward_description=program.reward_description,
            redeemed_by_owner_id=redeemed_by_owner_id,
        )
        session.add(redemption)

        code.status = RewardRedemptionCodeStatus.REDEEMED
        code.redeemed_at = now
        code.redeemed_by_owner_id = redeemed_by_owner_id

        card.cycle_number += 1
        card.punch_count = 0
        card.updated_at = now

        await session.commit()
        await session.refresh(redemption)
        await session.refresh(card)
        # Publishes the claim and repoints the card's NFT at its now-empty next cycle.
        await HederaLedger.record_redemption(session, card, redemption)
        return redemption
