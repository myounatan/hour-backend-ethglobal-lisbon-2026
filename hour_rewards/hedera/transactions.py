"""Every Hedera transaction this package submits, with no database involved.

Split from :mod:`hour_rewards.hedera.ledger` (which owns the "when" and the persistence) so
the ledger calls read as a sequence of intents, and so ``scripts/hedera_demo.py`` can walk
the whole punch-card lifecycle on testnet against the same code the service layer runs.

``hiero_sdk_python`` is imported lazily in each function: the punch-card tables must stay
importable for hosts that install this package without the ``hedera`` extra.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from hour_rewards.hedera.config import TOKEN_SYMBOL, HederaConfig

logger = logging.getLogger("hour_rewards.hedera")

# HTS caps a token's name at 100 characters.
MAX_TOKEN_NAME_LENGTH = 100


async def create_collection(config: HederaConfig, venue_name: str, venue_id: UUID) -> str:
    """Create a venue's punch-card NFT collection. Returns its token id."""
    from hiero_sdk_python import AccountId, SupplyType, TokenCreateTransaction, TokenType

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)
    metadata_key = parse_private_key(config.metadata_signing_key, config.operator_key_type)

    def submit() -> str:
        transaction = (
            TokenCreateTransaction()
            .set_token_name(f"{venue_name} Punch Card"[:MAX_TOKEN_NAME_LENGTH])
            .set_token_symbol(TOKEN_SYMBOL)
            .set_token_type(TokenType.NON_FUNGIBLE_UNIQUE)
            .set_decimals(0)
            .set_initial_supply(0)
            # Infinite: a venue mints one serial per user who earns a punch there, and it
            # can't know how many that will be when it opts in.
            .set_supply_type(SupplyType.INFINITE)
            .set_max_supply(0)
            .set_treasury_account_id(AccountId.from_string(config.operator_id))
            .set_admin_key(operator_key.public_key())
            .set_supply_key(operator_key.public_key())
            # HIP-657: a metadata key not set at creation can never be added, and without
            # one a card's progress could never be written back to its NFT.
            .set_metadata_key(metadata_key.public_key())
            .set_memo(f"hour-rewards:venue:{venue_id}")
            .freeze_with(client)
        )
        transaction.sign(operator_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return str(receipt.token_id)

    return await in_thread(submit)


async def create_topic(config: HederaConfig, venue_id: UUID) -> str:
    """Create a venue's punch ledger topic. Returns its topic id."""
    from hiero_sdk_python import TopicCreateTransaction

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)

    def submit() -> str:
        transaction = (
            TopicCreateTransaction()
            .set_memo(f"hour-rewards:punch-ledger:venue:{venue_id}")
            .set_admin_key(operator_key.public_key())
            .freeze_with(client)
        )
        transaction.sign(operator_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return str(receipt.topic_id)

    return await in_thread(submit)


async def create_account(config: HederaConfig, memo: str) -> Tuple[str, str, str]:
    """Create a custodial account. Returns ``(account_id, public_key, private_key_der)``."""
    from hiero_sdk_python import AccountCreateTransaction, PrivateKey

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)
    account_key = PrivateKey.generate_ed25519()

    def submit() -> str:
        transaction = (
            AccountCreateTransaction()
            .set_key_without_alias(account_key.public_key())
            .set_initial_balance(config.initial_account_balance)
            # Unlimited automatic associations (HIP-904) means a card can be transferred in
            # from any venue's collection without this account signing an association first.
            .set_max_automatic_token_associations(config.max_auto_associations)
            .set_account_memo(memo)
            .freeze_with(client)
        )
        transaction.sign(operator_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return str(receipt.account_id)

    account_id = await in_thread(submit)
    # DER carries its own algorithm identifier, so a stored key never needs a type beside it.
    return account_id, account_key.public_key().to_string_raw(), account_key.to_string_der()


async def mint_card(config: HederaConfig, token_id: str, metadata: bytes) -> int:
    """Mint one serial into a venue's collection. Returns the serial number."""
    from hiero_sdk_python import TokenId, TokenMintTransaction

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)

    def submit() -> int:
        transaction = (
            TokenMintTransaction()
            .set_token_id(TokenId.from_string(token_id))
            .set_metadata([metadata])
            .freeze_with(client)
        )
        transaction.sign(operator_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return int(receipt.serial_numbers[0])

    return await in_thread(submit)


async def transfer_card(
    config: HederaConfig, token_id: str, serial_number: int, recipient_account_id: str
) -> str:
    """Hand a freshly minted card from the treasury to its owner. Returns the tx id."""
    from hiero_sdk_python import AccountId, NftId, TokenId, TransferTransaction

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)
    nft_id = NftId(TokenId.from_string(token_id), serial_number)
    treasury = AccountId.from_string(config.operator_id)
    recipient = AccountId.from_string(recipient_account_id)

    def submit() -> str:
        transaction = (
            TransferTransaction().add_nft_transfer(nft_id, treasury, recipient).freeze_with(client)
        )
        transaction.sign(operator_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return str(receipt.transaction_id)

    return await in_thread(submit)


async def update_card_metadata(
    config: HederaConfig, token_id: str, serial_number: int, metadata: bytes
) -> str:
    """Point a card's NFT at fresh metadata (HIP-657). Returns the tx id."""
    from hiero_sdk_python import TokenId, TokenUpdateNftsTransaction

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    metadata_key = parse_private_key(config.metadata_signing_key, config.operator_key_type)

    def submit() -> str:
        transaction = (
            TokenUpdateNftsTransaction()
            .set_token_id(TokenId.from_string(token_id))
            .set_serial_numbers([serial_number])
            .set_metadata(metadata)
            .freeze_with(client)
        )
        # Once a card has left the treasury, only the metadata key can rewrite it (HIP-850).
        transaction.sign(metadata_key)
        receipt = transaction.execute(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        return str(receipt.transaction_id)

    return await in_thread(submit)


async def submit_message(
    config: HederaConfig, topic_id: str, message: Dict[str, Any]
) -> Tuple[int, Optional[str]]:
    """Publish one punch-ledger message. Returns ``(sequence_number, consensus_timestamp)``."""
    from hiero_sdk_python import TopicId, TopicMessageSubmitTransaction

    from hour_rewards.hedera.client import (
        TRANSACTION_TIMEOUT_SECONDS,
        get_client,
        in_thread,
        parse_private_key,
    )

    client = get_client(config)
    operator_key = parse_private_key(config.operator_key, config.operator_key_type)
    payload = json.dumps(message, separators=(",", ":"), sort_keys=True)

    def submit() -> Tuple[int, Optional[str]]:
        transaction = (
            TopicMessageSubmitTransaction()
            .set_topic_id(TopicId.from_string(topic_id))
            .set_message(payload)
            .freeze_with(client)
        )
        transaction.sign(operator_key)
        response = transaction.execute(
            client, timeout=TRANSACTION_TIMEOUT_SECONDS, wait_for_receipt=False
        )
        receipt = response.get_receipt(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
        sequence_number = int(receipt.topic_sequence_number)
        # The receipt carries the sequence number but not the consensus timestamp, so the
        # record is fetched separately -- and optionally, since the message is already
        # addressable by topic + sequence without it.
        try:
            record = response.get_record(client, timeout=TRANSACTION_TIMEOUT_SECONDS)
            return sequence_number, str(record.consensus_timestamp)
        except Exception as error:
            logger.debug("Consensus timestamp unavailable for topic %s: %s", topic_id, error)
            return sequence_number, None

    return await in_thread(submit)
