"""Punch cards on Hedera: an HTS collection per venue, one NFT per card, HCS as the log.

What lives where, and why:

- **A venue opting in creates its own HTS NFT collection** and an HCS topic. Per-venue
  rather than one global collection, so a card appears in a wallet as that venue's, and each
  venue's punch history is its own auditable stream.
- **A user's card is one serial in that collection**, minted on their first verified punch
  and transferred to a custodial account created for them. It is never burned and re-minted:
  redeeming rewrites its metadata, exactly as the card row survives a cycle reset.
- **Progress lives in metadata, proof lives on HCS.** Every verified punch and every
  redemption submits a message to the venue's topic and points the NFT at a fresh metadata
  URI (``TokenUpdateNftsTransaction``, HIP-657), so a card's on-chain history is a sequence
  of signed transactions rather than edits behind a stable pointer.

Everything here is best-effort by design: the database is the source of truth, and a Hedera
failure is logged rather than surfaced to a user mid-punch. What keeps that from losing
things is that every id is committed the moment it exists -- a token before its topic is
attempted, a serial before it is transferred -- so nothing that exists on the ledger is
missing from the database, and whatever was *left* undone shows up as a null column for
:meth:`HederaLedger.reconcile` to finish later.

A message can be published twice if a submission timed out ambiguously and was then
recovered by a reconcile; every message carries the ``event`` or ``redemption`` uuid it
belongs to, so a consumer reading a venue's topic deduplicates on that.

No Solidity and no smart contracts -- only the native token, consensus and account services
via ``hiero_sdk_python``.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TypeVar
from uuid import UUID

from sqlalchemy import and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from hour_rewards.base import TimestampedModel, utc_now
from hour_rewards.hedera import transactions
from hour_rewards.hedera.config import HederaConfig, get_hedera_config
from hour_rewards.hedera.keys import encrypt_private_key, ledger_user_ref
from hour_rewards.hedera.metadata import card_metadata_uri
from hour_rewards.hedera.transactions import MAX_TOKEN_NAME_LENGTH
from hour_rewards.models.hedera_account import HederaAccount
from hour_rewards.models.punch_card import PunchCard
from hour_rewards.models.punch_event import PunchEvent, PunchEventStatus
from hour_rewards.models.reward_program import RewardProgram
from hour_rewards.models.reward_redemption import RewardRedemption

logger = logging.getLogger("hour_rewards.hedera")

# Bumped if the shape of the JSON published to a topic changes, so a consumer reading a
# venue's whole history can tell which schema each message was written under.
MESSAGE_VERSION = 1

# How many rows of each kind one reconcile pass works through.
DEFAULT_RECONCILE_LIMIT = 50

RowT = TypeVar("RowT", bound=TimestampedModel)


class HederaLedger:
    """Mirrors punch-card state onto Hedera. Every method is a no-op when unconfigured."""

    @staticmethod
    async def ensure_program_ledger(session: AsyncSession, program: RewardProgram) -> RewardProgram:
        """Give a venue's program its NFT collection and punch topic, if it has none yet."""
        try:
            return await HederaLedger._ensure_program_ledger(session, program)
        except Exception as error:
            await _abandon(session, f"ledger setup for venue {program.venue_id}", error)
            return program

    @staticmethod
    async def ensure_user_account(session: AsyncSession, user_id: UUID) -> Optional[HederaAccount]:
        """The user's custodial account, created (and its key encrypted) on first need."""
        config = get_hedera_config()
        if config is None:
            return None

        result = await session.execute(
            select(HederaAccount).where(
                HederaAccount.user_id == user_id, HederaAccount.network == config.network
            )
        )
        account = result.scalar_one_or_none()
        if account is not None:
            return account

        memo = f"hour-rewards:user:{ledger_user_ref(user_id, config.key_encryption_secret)}"
        try:
            account_id, public_key, private_key_der = await transactions.create_account(
                config, memo
            )
        except Exception as error:
            logger.warning("Hedera account creation failed for user %s: %s", user_id, error)
            return None

        account = HederaAccount(
            user_id=user_id,
            network=config.network,
            account_id=account_id,
            public_key=public_key,
            encrypted_private_key=encrypt_private_key(
                private_key_der, config.key_encryption_secret
            ),
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        logger.info("Created custodial Hedera account %s for user %s", account_id, user_id)
        return account

    @staticmethod
    async def ensure_card_nft(session: AsyncSession, card: PunchCard) -> Tuple[PunchCard, bool]:
        """Mint this card's NFT into its venue's collection and hand it to its user.

        Both halves are resumable: the serial is committed before the transfer is attempted,
        so a card that was minted but never handed over gets finished by a later call rather
        than minted a second time. Returns the card and whether *this* call minted it, which
        is what lets a caller skip a metadata update the mint already covered.
        """
        config = get_hedera_config()
        already_held = card.hedera_nft_serial is not None and card.hedera_nft_account_id is not None
        if config is None or already_held:
            return card, False

        program = await _program_for_venue(session, card.venue_id)
        if program is None or not program.hedera_token_id:
            return card, False
        account = await HederaLedger.ensure_user_account(session, card.user_id)
        if account is None:
            return card, False

        minted_now = False
        if card.hedera_nft_serial is None:
            try:
                metadata = card_metadata_uri(config, card.id, card.cycle_number, card.punch_count)
                serial_number = await transactions.mint_card(
                    config, program.hedera_token_id, metadata
                )
            except Exception as error:
                logger.warning("Hedera mint failed for punch card %s: %s", card.id, error)
                return card, False
            # Committed before the transfer is attempted: a serial that exists on the ledger
            # and nowhere in the database is one this card would mint all over again.
            card = await _save(session, card, hedera_nft_serial=serial_number)
            minted_now = True
            logger.info(
                "Minted punch card %s as %s/%s", card.id, program.hedera_token_id, serial_number
            )

        if card.hedera_nft_account_id is None and card.hedera_nft_serial is not None:
            try:
                await transactions.transfer_card(
                    config, program.hedera_token_id, card.hedera_nft_serial, account.account_id
                )
            except Exception as error:
                logger.warning("Hedera transfer failed for punch card %s: %s", card.id, error)
                return card, minted_now
            card = await _save(session, card, hedera_nft_account_id=account.account_id)
            logger.info("Handed punch card %s to %s", card.id, account.account_id)

        return card, minted_now

    @staticmethod
    async def record_punch(
        session: AsyncSession, card: PunchCard, punch_event: Optional[PunchEvent] = None
    ) -> PunchCard:
        """Publish a verified punch to the venue's topic and advance the NFT's metadata.

        Mints the card first if this is its owner's first punch here, which is why nothing
        waits on the network until a punch actually lands.
        """
        try:
            return await HederaLedger._record_punch(session, card, punch_event)
        except Exception as error:
            await _abandon(session, f"punch mirror for card {card.id}", error)
            return card

    @staticmethod
    async def record_redemption(
        session: AsyncSession, card: PunchCard, redemption: RewardRedemption
    ) -> None:
        """Publish a claimed reward, and reflect the card's fresh cycle in its metadata."""
        try:
            await HederaLedger._record_redemption(session, card, redemption)
        except Exception as error:
            await _abandon(session, f"redemption mirror for card {card.id}", error)

    @staticmethod
    async def reconcile(
        session: AsyncSession, limit: int = DEFAULT_RECONCILE_LIMIT
    ) -> Dict[str, int]:
        """Finish what an earlier failure left undone, and report how much it fixed.

        Everything still pending is already visible in the database as a null: a program with
        no token or topic, a card with no serial or no holder, a verified punch or a claimed
        reward with no sequence number. Nothing here is scheduled -- it is meant to be run
        after an outage, or before a demo (see ``backend/scripts/hedera_reconcile.py``).
        """
        counts = {
            "programs": 0,
            "mints": 0,
            "transfers": 0,
            "punches": 0,
            "redemptions": 0,
            "metadata": 0,
        }
        config = get_hedera_config()
        if config is None:
            return counts

        programs: Dict[UUID, Optional[RewardProgram]] = {}

        async def program_for(venue_id: UUID) -> Optional[RewardProgram]:
            if venue_id not in programs:
                programs[venue_id] = await _program_for_venue(session, venue_id)
            return programs[venue_id]

        # Cards whose on-chain state moved during this pass, so their metadata is refreshed
        # once at the end rather than once per replayed message.
        stale_metadata: Dict[UUID, PunchCard] = {}

        counts["programs"] = await _reconcile_programs(session, limit, programs)

        for pending_card in await _cards_to_finish(session, limit):
            had_serial = pending_card.hedera_nft_serial is not None
            pending_card, _ = await HederaLedger.ensure_card_nft(session, pending_card)
            if pending_card.hedera_nft_account_id is None:
                continue
            counts["transfers" if had_serial else "mints"] += 1
            stale_metadata[pending_card.id] = pending_card

        for event in await _punches_to_publish(session, limit):
            card = await session.get(PunchCard, event.punch_card_id)
            program = await program_for(card.venue_id) if card is not None else None
            if card is None or program is None or not program.hedera_topic_id:
                continue
            message = {
                **_card_message(config, card, cycle_number=event.cycle_number, at=event.created_at),
                "type": "punch",
                "count": await _punch_ordinal(session, event),
                "required": program.punches_required,
                **_punch_proof(event),
                # Recovered late, so its consensus timestamp is when it was published rather
                # than when the punch was earned -- which is what "at" carries.
                "backfill": True,
            }
            await _publish(session, config, program, card, message, event, update_metadata=False)
            if event.hedera_topic_sequence_number is not None:
                counts["punches"] += 1
                stale_metadata[card.id] = card

        for redemption in await _redemptions_to_publish(session, limit):
            card = await session.get(PunchCard, redemption.punch_card_id)
            program = await program_for(card.venue_id) if card is not None else None
            if card is None or program is None or not program.hedera_topic_id:
                continue
            message = {
                **_redemption_message(config, card, redemption, at=redemption.created_at),
                "backfill": True,
            }
            await _publish(
                session, config, program, card, message, redemption, update_metadata=False
            )
            if redemption.hedera_topic_sequence_number is not None:
                counts["redemptions"] += 1
                stale_metadata[card.id] = card

        counts["metadata"] = await _refresh_metadata(session, config, stale_metadata.values())
        logger.info("Hedera reconcile finished: %s", counts)
        return counts

    @staticmethod
    async def _ensure_program_ledger(
        session: AsyncSession, program: RewardProgram
    ) -> RewardProgram:
        config = get_hedera_config()
        if config is None or (program.hedera_token_id and program.hedera_topic_id):
            return program

        if not program.hedera_token_id:
            venue_name = await _venue_name(session, program.venue_id)
            try:
                token_id = await transactions.create_collection(
                    config, venue_name, program.venue_id
                )
            except Exception as error:
                logger.warning(
                    "Hedera collection creation failed for venue %s: %s", program.venue_id, error
                )
                return program
            # Saved on its own, so a topic failing next can't lose a collection that exists.
            program = await _save(session, program, hedera_token_id=token_id)
            logger.info(
                "Created Hedera punch-card collection %s for venue %s", token_id, program.venue_id
            )

        if not program.hedera_topic_id:
            try:
                topic_id = await transactions.create_topic(config, program.venue_id)
            except Exception as error:
                logger.warning(
                    "Hedera topic creation failed for venue %s: %s", program.venue_id, error
                )
                return program
            program = await _save(session, program, hedera_topic_id=topic_id)
            logger.info("Created Hedera punch topic %s for venue %s", topic_id, program.venue_id)

        return program

    @staticmethod
    async def _record_punch(
        session: AsyncSession, card: PunchCard, punch_event: Optional[PunchEvent]
    ) -> PunchCard:
        config = get_hedera_config()
        if config is None:
            return card
        program = await _program_for_venue(session, card.venue_id)
        if program is None:
            return card

        card, minted_now = await HederaLedger.ensure_card_nft(session, card)

        message: Dict[str, Any] = {
            **_card_message(config, card),
            "type": "punch",
            "count": card.punch_count,
            "required": program.punches_required,
        }
        if punch_event is not None:
            message.update(_punch_proof(punch_event))

        await _publish(
            session,
            config,
            program,
            card,
            message,
            proof_row=punch_event,
            # A mint that just wrote this punch's metadata doesn't need it written again.
            update_metadata=not minted_now,
        )
        return card

    @staticmethod
    async def _record_redemption(
        session: AsyncSession, card: PunchCard, redemption: RewardRedemption
    ) -> None:
        config = get_hedera_config()
        if config is None:
            return
        program = await _program_for_venue(session, card.venue_id)
        if program is None:
            return

        await _publish(
            session,
            config,
            program,
            card,
            _redemption_message(config, card, redemption),
            proof_row=redemption,
        )


def _card_message(
    config: HederaConfig,
    card: PunchCard,
    cycle_number: Optional[int] = None,
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The fields every punch-ledger message carries, with a salted stand-in for the user.

    ``cycle_number`` and ``at`` get overridden when replaying something that happened
    earlier, so a recovered message describes the moment it belongs to.
    """
    return {
        "v": MESSAGE_VERSION,
        "card": str(card.id),
        "venue": str(card.venue_id),
        "user": ledger_user_ref(card.user_id, config.key_encryption_secret),
        "cycle": cycle_number if cycle_number is not None else card.cycle_number,
        "at": (at or utc_now()).isoformat(timespec="seconds"),
    }


def _punch_proof(punch_event: PunchEvent) -> Dict[str, Any]:
    """Which submission earned a punch: its receipt hash, never anything off the receipt."""
    return {"event": str(punch_event.id), "receipt": punch_event.dedupe_hash}


def _redemption_message(
    config: HederaConfig,
    card: PunchCard,
    redemption: RewardRedemption,
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    return {
        # The cycle that was claimed, not the empty one the card has now moved on to.
        **_card_message(config, card, cycle_number=redemption.cycle_number, at=at),
        "type": "redeem",
        "required": redemption.punches_required,
        "reward": redemption.reward_description,
        "redemption": str(redemption.id),
    }


async def _publish(
    session: AsyncSession,
    config: HederaConfig,
    program: RewardProgram,
    card: PunchCard,
    message: Dict[str, Any],
    proof_row: Optional[Any],
    update_metadata: bool = True,
) -> None:
    """Submit one topic message, refresh the card's metadata, and record both references."""
    sequence_number: Optional[int] = None
    consensus_timestamp: Optional[str] = None
    metadata_tx_id: Optional[str] = None

    if program.hedera_topic_id:
        try:
            sequence_number, consensus_timestamp = await transactions.submit_message(
                config, program.hedera_topic_id, message
            )
        except Exception as error:
            logger.warning("Hedera topic submit failed for punch card %s: %s", card.id, error)

    if update_metadata and program.hedera_token_id and card.hedera_nft_serial is not None:
        try:
            metadata = card_metadata_uri(config, card.id, card.cycle_number, card.punch_count)
            metadata_tx_id = await transactions.update_card_metadata(
                config, program.hedera_token_id, card.hedera_nft_serial, metadata
            )
        except Exception as error:
            logger.warning("Hedera metadata update failed for punch card %s: %s", card.id, error)

    if proof_row is None or (sequence_number is None and metadata_tx_id is None):
        return

    # Only what this call actually got back is written, so replaying a message can never
    # clear a reference an earlier attempt already earned.
    if sequence_number is not None:
        proof_row.hedera_topic_sequence_number = sequence_number
        proof_row.hedera_consensus_timestamp = consensus_timestamp
    if metadata_tx_id is not None:
        proof_row.hedera_metadata_tx_id = metadata_tx_id
    proof_row.updated_at = utc_now()
    await session.commit()
    await session.refresh(proof_row)


async def _save(session: AsyncSession, row: RowT, **fields: Any) -> RowT:
    """Write fields onto a row and commit them right away -- see this module's docstring."""
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = utc_now()
    await session.commit()
    await session.refresh(row)
    return row


async def _abandon(session: AsyncSession, what: str, error: Exception) -> None:
    """Give up on mirroring one thing, leaving the session usable by the caller.

    Only a session the failure actually broke gets rolled back -- one whose transaction is
    still good is left alone, since a rollback expires every object the caller is holding.
    Whatever the database had to say is committed before any of this runs, so the rollback
    can only ever discard the mirror's own half-written work.
    """
    logger.warning("Hedera %s failed: %s", what, error)
    if session.is_active:
        return
    try:
        await session.rollback()
    except Exception as rollback_error:
        logger.debug("Rollback after a Hedera failure also failed: %s", rollback_error)


async def _reconcile_programs(
    session: AsyncSession, limit: int, cache: Dict[UUID, Optional[RewardProgram]]
) -> int:
    """Opt-ins whose collection or topic never got created."""
    result = await session.execute(
        select(RewardProgram)
        .where(
            RewardProgram.is_enabled == True,  # noqa: E712
            or_(
                col(RewardProgram.hedera_token_id).is_(None),
                col(RewardProgram.hedera_topic_id).is_(None),
            ),
        )
        .limit(limit)
    )
    fixed = 0
    for program in result.scalars().all():
        program = await HederaLedger.ensure_program_ledger(session, program)
        cache[program.venue_id] = program
        if program.hedera_token_id and program.hedera_topic_id:
            fixed += 1
    return fixed


async def _cards_to_finish(session: AsyncSession, limit: int) -> List[PunchCard]:
    """Cards that earned an NFT but have none, and cards whose NFT never left the treasury.

    "Earned" is any verified punch ever, not current progress: a card whose cycle has since
    been redeemed still deserves the card it filled.
    """
    earned_a_punch = (
        select(PunchEvent.id)
        .where(
            PunchEvent.punch_card_id == PunchCard.id,
            PunchEvent.status == PunchEventStatus.VERIFIED,
        )
        .exists()
    )
    result = await session.execute(
        select(PunchCard)
        .where(
            or_(
                and_(col(PunchCard.hedera_nft_serial).is_(None), earned_a_punch),
                and_(
                    col(PunchCard.hedera_nft_serial).is_not(None),
                    col(PunchCard.hedera_nft_account_id).is_(None),
                ),
            )
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def _punches_to_publish(session: AsyncSession, limit: int) -> List[PunchEvent]:
    """Verified punches that never reached their venue's topic, oldest first."""
    result = await session.execute(
        select(PunchEvent)
        .where(
            PunchEvent.status == PunchEventStatus.VERIFIED,
            col(PunchEvent.hedera_topic_sequence_number).is_(None),
        )
        .order_by(col(PunchEvent.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def _redemptions_to_publish(session: AsyncSession, limit: int) -> List[RewardRedemption]:
    """Claimed rewards that never reached their venue's topic, oldest first."""
    result = await session.execute(
        select(RewardRedemption)
        .where(
            col(RewardRedemption.punch_card_id).is_not(None),
            col(RewardRedemption.hedera_topic_sequence_number).is_(None),
        )
        .order_by(col(RewardRedemption.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def _refresh_metadata(session: AsyncSession, config: HederaConfig, cards: Any) -> int:
    """Point each card's NFT at its state *now*: metadata is a projection, not a history."""
    updated = 0
    for card in cards:
        program = await _program_for_venue(session, card.venue_id)
        if program is None or not program.hedera_token_id or card.hedera_nft_serial is None:
            continue
        try:
            metadata = card_metadata_uri(config, card.id, card.cycle_number, card.punch_count)
            await transactions.update_card_metadata(
                config, program.hedera_token_id, card.hedera_nft_serial, metadata
            )
        except Exception as error:
            logger.warning("Hedera metadata update failed for punch card %s: %s", card.id, error)
            continue
        updated += 1
    return updated


async def _punch_ordinal(session: AsyncSession, punch_event: PunchEvent) -> int:
    """Which punch of its cycle an event was, so a replayed message counts truthfully."""
    result = await session.execute(
        select(PunchEvent.id).where(
            PunchEvent.punch_card_id == punch_event.punch_card_id,
            PunchEvent.cycle_number == punch_event.cycle_number,
            PunchEvent.status == PunchEventStatus.VERIFIED,
            col(PunchEvent.created_at) <= punch_event.created_at,
        )
    )
    return len(result.scalars().all())


async def _program_for_venue(session: AsyncSession, venue_id: UUID) -> Optional[RewardProgram]:
    result = await session.execute(select(RewardProgram).where(RewardProgram.venue_id == venue_id))
    return result.scalar_one_or_none()


async def _venue_name(session: AsyncSession, venue_id: UUID) -> str:
    """Read off the host's ``venues`` table, so a collection carries the venue's own name."""
    result = await session.execute(
        text("SELECT name FROM venues WHERE id = :venue_id"), {"venue_id": venue_id}
    )
    row = result.first()
    return (row[0] if row and row[0] else "Venue")[:MAX_TOKEN_NAME_LENGTH]
