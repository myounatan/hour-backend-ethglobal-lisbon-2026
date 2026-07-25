"""Walk one punch card through its whole life on Hedera testnet, with no database.

Runs the same functions the service layer runs (:mod:`hour_rewards.hedera.transactions`),
in the same order, so what a judge watches here is what the app does in production:

    venue opts in   -> TokenCreateTransaction (NFT collection) + TopicCreateTransaction
    first punch     -> AccountCreateTransaction (custodial) + TokenMintTransaction + transfer
    each punch      -> TopicMessageSubmitTransaction + TokenUpdateNftsTransaction
    reward claimed  -> TopicMessageSubmitTransaction + TokenUpdateNftsTransaction (next cycle)

Usage::

    pip install -e "vendor/hour-rewards-sdk[hedera]"
    export HEDERA_OPERATOR_ID=0.0.xxxxx HEDERA_OPERATOR_KEY=0x...
    python scripts/hedera_demo.py

Free testnet credentials: https://portal.hedera.com. Every id printed is a HashScan link.
"""

import argparse
import asyncio
import os
import sys
from typing import Optional
from uuid import uuid4

from hour_rewards.hedera import HederaConfig, close_hedera_clients, configure_hedera, transactions
from hour_rewards.hedera.config import DEFAULT_NETWORK
from hour_rewards.hedera.keys import decrypt_private_key, encrypt_private_key, ledger_user_ref
from hour_rewards.hedera.ledger import MESSAGE_VERSION
from hour_rewards.hedera.metadata import card_metadata_uri

# Any URL works for a demo: nothing resolves it here, and the point is that the 100 bytes
# on-chain are a pointer at the host's API rather than the JSON itself.
DEMO_METADATA_BASE_URL = "https://api.get-hour.com/api/rewards/nft"
DEMO_VENUE_NAME = "Bar Ambar"
DEMO_PUNCHES_REQUIRED = 3


def _config() -> HederaConfig:
    config = HederaConfig.build(
        operator_id=os.environ.get("HEDERA_OPERATOR_ID"),
        operator_key=os.environ.get("HEDERA_OPERATOR_KEY"),
        metadata_base_url=os.environ.get("HEDERA_NFT_METADATA_BASE_URL", DEMO_METADATA_BASE_URL),
        key_encryption_secret=os.environ.get("HEDERA_KEY_ENCRYPTION_SECRET", "demo-secret"),
        network=os.environ.get("HEDERA_NETWORK", DEFAULT_NETWORK),
        operator_key_type=os.environ.get("HEDERA_OPERATOR_KEY_TYPE"),
    )
    if config is None:
        sys.exit("Set HEDERA_OPERATOR_ID and HEDERA_OPERATOR_KEY (https://portal.hedera.com).")
    return config


def _step(number: int, title: str) -> None:
    print(f"\n{number}. {title}")


async def demo(config: HederaConfig, punches_required: int) -> None:
    venue_id, user_id, card_id = uuid4(), uuid4(), uuid4()
    print(f"Network: {config.network}  Operator: {config.operator_id}")
    print(f"Venue {venue_id}\nUser  {user_id}\nCard  {card_id}")

    _step(1, f"{DEMO_VENUE_NAME} opts in: its own NFT collection and punch ledger topic")
    token_id = await transactions.create_collection(config, DEMO_VENUE_NAME, venue_id)
    topic_id = await transactions.create_topic(config, venue_id)
    print(f"   collection {token_id}  https://hashscan.io/{config.network}/token/{token_id}")
    print(f"   topic      {topic_id}  {config.hashscan_topic_url(topic_id)}")

    _step(2, "First punch: a custodial account for a user who has never held a wallet")
    user_ref = ledger_user_ref(user_id, config.key_encryption_secret)
    account_id, public_key, private_key_der = await transactions.create_account(
        config, f"hour-rewards:user:{user_ref}"
    )
    encrypted = encrypt_private_key(private_key_der, config.key_encryption_secret)
    assert decrypt_private_key(encrypted, config.key_encryption_secret) == private_key_der
    print(f"   account {account_id}  https://hashscan.io/{config.network}/account/{account_id}")
    print(f"   key stored encrypted ({len(encrypted)} chars), never in plaintext")

    _step(3, "Mint the card into the venue's collection, then transfer it to that account")
    metadata = card_metadata_uri(config, card_id, cycle_number=1, punch_count=0)
    serial_number = await transactions.mint_card(config, token_id, metadata)
    await transactions.transfer_card(config, token_id, serial_number, account_id)
    print(f"   {token_id}/{serial_number}  {config.hashscan_nft_url(token_id, serial_number)}")
    print(f"   metadata -> {metadata.decode()} ({len(metadata)} of 100 bytes)")

    _step(4, f"Earning {punches_required} punches: one topic message and one metadata update each")
    for punch_count in range(1, punches_required + 1):
        sequence_number, consensus_timestamp = await transactions.submit_message(
            config,
            topic_id,
            {
                "v": MESSAGE_VERSION,
                "type": "punch",
                "card": str(card_id),
                "venue": str(venue_id),
                "user": user_ref,
                "cycle": 1,
                "count": punch_count,
                "required": punches_required,
            },
        )
        metadata = card_metadata_uri(config, card_id, cycle_number=1, punch_count=punch_count)
        await transactions.update_card_metadata(config, token_id, serial_number, metadata)
        print(
            f"   punch {punch_count}/{punches_required}: topic seq {sequence_number}"
            f" at {consensus_timestamp or 'n/a'}, metadata v1-{punch_count}"
        )

    _step(5, "Owner scans the QR: the claim is published and the card rolls into cycle 2")
    sequence_number, consensus_timestamp = await transactions.submit_message(
        config,
        topic_id,
        {
            "v": MESSAGE_VERSION,
            "type": "redeem",
            "card": str(card_id),
            "venue": str(venue_id),
            "user": user_ref,
            "cycle": 1,
            "required": punches_required,
            "reward": "Free appetizer",
            "redemption": str(uuid4()),
        },
    )
    metadata = card_metadata_uri(config, card_id, cycle_number=2, punch_count=0)
    await transactions.update_card_metadata(config, token_id, serial_number, metadata)
    print(f"   redeemed: topic seq {sequence_number} at {consensus_timestamp or 'n/a'}")
    print(f"   same NFT, next cycle: metadata -> {metadata.decode()}")

    print("\nVerify:")
    print(f"   card    {config.hashscan_nft_url(token_id, serial_number)}")
    print(f"   ledger  {config.hashscan_topic_url(topic_id)}")
    print(f"   holder  https://hashscan.io/{config.network}/account/{account_id}")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--punches",
        type=int,
        default=DEMO_PUNCHES_REQUIRED,
        help=f"punches to earn before redeeming (default {DEMO_PUNCHES_REQUIRED})",
    )
    args = parser.parse_args(argv)
    config = _config()
    configure_hedera(config)
    try:
        asyncio.run(demo(config, args.punches))
    finally:
        close_hedera_clients()


if __name__ == "__main__":
    main()
