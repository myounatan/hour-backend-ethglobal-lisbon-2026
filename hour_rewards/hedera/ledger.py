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

Everything here is best-effort by design. The database is the source of truth; a Hedera
failure is logged and left for the next call to pick up (each step is guarded by "is this
already recorded?"), never surfaced to a user mid-punch. No Solidity and no smart contracts
-- only the native token, consensus and account services via ``hiero_sdk_python``.
"""

import logging
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from hour_rewards.base import utc_now
from hour_rewards.hedera import transactions
from hour_rewards.hedera.config import HederaConfig, get_hedera_config
from hour_rewards.hedera.keys import encrypt_private_key, ledger_user_ref
from hour_rewards.hedera.metadata import card_metadata_uri
from hour_rewards.hedera.transactions import MAX_TOKEN_NAME_LENGTH
from hour_rewards.models.hedera_account import HederaAccount
from hour_rewards.models.punch_card import PunchCard
from hour_rewards.models.punch_event import PunchEvent
from hour_rewards.models.reward_program import RewardProgram
from hour_rewards.models.reward_redemption import RewardRedemption

logger = logging.getLogger("hour_rewards.hedera")

# Bumped if the shape of the JSON published to a topic changes, so a consumer reading a
# venue's whole history can tell which schema each message was written under.
MESSAGE_VERSION = 1


class HederaLedger:
    """Mirrors punch-card state onto Hedera. Every method is a no-op when unconfigured."""

    @staticmethod
    async def ensure_program_ledger(session: AsyncSession, program: RewardProgram) -> RewardProgram:
        """Give a venue's program its NFT collection and punch topic, if it has none yet."""
        config = get_hedera_config()
        if config is None or (program.hedera_token_id and program.hedera_topic_id):
            return program

        try:
            if not program.hedera_token_id:
                venue_name = await _venue_name(session, program.venue_id)
                program.hedera_token_id = await transactions.create_collection(
                    config, venue_name, program.venue_id
                )
                logger.info(
                    "Created Hedera punch-card collection %s for venue %s",
                    program.hedera_token_id,
                    program.venue_id,
                )
            if not program.hedera_topic_id:
                program.hedera_topic_id = await transactions.create_topic(config, program.venue_id)
                logger.info(
                    "Created Hedera punch topic %s for venue %s",
                    program.hedera_topic_id,
                    program.venue_id,
                )
        except Exception as error:
            logger.warning("Hedera setup failed for venue %s: %s", program.venue_id, error)
            await session.rollback()
            return program

        program.updated_at = utc_now()
        await session.commit()
        await session.refresh(program)
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

        Returns the card and whether *this* call minted it, which is what lets a caller skip
        a metadata update that the mint already covered.
        """
        config = get_hedera_config()
        if config is None or card.hedera_nft_serial is not None:
            return card, False

        program = await _program_for_venue(session, card.venue_id)
        if program is None or not program.hedera_token_id:
            return card, False
        account = await HederaLedger.ensure_user_account(session, card.user_id)
        if account is None:
            return card, False

        try:
            metadata = card_metadata_uri(config, card.id, card.cycle_number, card.punch_count)
            serial_number = await transactions.mint_card(config, program.hedera_token_id, metadata)
            await transactions.transfer_card(
                config, program.hedera_token_id, serial_number, account.account_id
            )
        except Exception as error:
            logger.warning("Hedera mint failed for punch card %s: %s", card.id, error)
            return card, False

        card.hedera_nft_serial = serial_number
        card.updated_at = utc_now()
        await session.commit()
        await session.refresh(card)
        logger.info(
            "Minted punch card %s as %s/%s to %s",
            card.id,
            program.hedera_token_id,
            serial_number,
            account.account_id,
        )
        return card, True

    @staticmethod
    async def record_punch(
        session: AsyncSession, card: PunchCard, punch_event: Optional[PunchEvent] = None
    ) -> PunchCard:
        """Publish a verified punch to the venue's topic and advance the NFT's metadata.

        Mints the card first if this is its owner's first punch here, which is why nothing
        waits on the network until a punch actually lands.
        """
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
            message["event"] = str(punch_event.id)
            # The receipt hash, not the receipt: proves which submission earned the punch
            # without publishing anything off the user's bill.
            message["receipt"] = punch_event.dedupe_hash

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
    async def record_redemption(
        session: AsyncSession, card: PunchCard, redemption: RewardRedemption
    ) -> None:
        """Publish a claimed reward, and reflect the card's fresh cycle in its metadata."""
        config = get_hedera_config()
        if config is None:
            return
        program = await _program_for_venue(session, card.venue_id)
        if program is None:
            return

        message = {
            **_card_message(config, card),
            "type": "redeem",
            # The cycle that was claimed, not the empty one the card has now moved on to.
            "cycle": redemption.cycle_number,
            "required": redemption.punches_required,
            "reward": redemption.reward_description,
            "redemption": str(redemption.id),
        }

        await _publish(session, config, program, card, message, proof_row=redemption)


def _card_message(config: HederaConfig, card: PunchCard) -> Dict[str, Any]:
    """The fields every punch-ledger message carries, with a salted stand-in for the user."""
    return {
        "v": MESSAGE_VERSION,
        "card": str(card.id),
        "venue": str(card.venue_id),
        "user": ledger_user_ref(card.user_id, config.key_encryption_secret),
        "cycle": card.cycle_number,
        "at": utc_now().isoformat(timespec="seconds"),
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

    if proof_row is None or not any((sequence_number, consensus_timestamp, metadata_tx_id)):
        return

    proof_row.hedera_topic_sequence_number = sequence_number
    proof_row.hedera_consensus_timestamp = consensus_timestamp
    proof_row.hedera_metadata_tx_id = metadata_tx_id
    proof_row.updated_at = utc_now()
    await session.commit()
    await session.refresh(proof_row)


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
