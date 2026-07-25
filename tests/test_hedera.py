"""Unit tests for the Hedera layer's pure parts: gating, URIs, keys, message shape.

Nothing here touches a network or a database, so they run with no credentials and without
the ``hedera`` extra installed. The database-orchestration side (minting on first punch,
writing references back) is exercised by the host app's ``test_reward_hedera.py``, since it
needs the host's ``users``/``venues`` tables -- same split as ``test_reward_service.py``.
"""

import base64
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hour_rewards.hedera import HederaConfig, HederaLedger, configure_hedera, get_hedera_config
from hour_rewards.hedera.config import MAX_METADATA_BYTES
from hour_rewards.hedera.keys import decrypt_private_key, encrypt_private_key, ledger_user_ref
from hour_rewards.hedera.ledger import MESSAGE_VERSION, _card_message
from hour_rewards.hedera.mirror import _decode_message
from hour_rewards.hedera.metadata import MetadataTooLargeError, card_metadata_uri

SECRET = "test-secret"


def fake_card(cycle_number: int = 1, punch_count: int = 0) -> SimpleNamespace:
    """A card's fields without a mapped ``PunchCard``.

    Instantiating the real model configures SQLAlchemy's mappers, which need the host's
    ``User``/``Venue``/``UserImage`` classes -- unavailable here by design (see the module
    docstring). Everything under test reads plain attributes off a card.
    """
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        venue_id=uuid4(),
        cycle_number=cycle_number,
        punch_count=punch_count,
        hedera_nft_serial=None,
    )


@pytest.fixture
def config() -> HederaConfig:
    built = HederaConfig.build(
        operator_id="0.0.12345",
        operator_key="0xabc123",
        metadata_base_url="https://api.example.com/api/rewards/nft/",
        key_encryption_secret=SECRET,
    )
    assert built is not None
    return built


@pytest.fixture(autouse=True)
def unconfigured():
    """Leave the layer dormant unless a test configures it, and never leak config."""
    configure_hedera(None)
    yield
    configure_hedera(None)


def test_build_returns_none_when_any_credential_is_missing():
    assert HederaConfig.build(None, "key", "https://x.dev", "secret") is None
    assert HederaConfig.build("0.0.1", None, "https://x.dev", "secret") is None
    assert HederaConfig.build("0.0.1", "key", None, "secret") is None
    assert HederaConfig.build("0.0.1", "key", "https://x.dev", None) is None


def test_build_drops_unset_optionals_and_trims_the_base_url(config: HederaConfig):
    # A trailing slash would double up in every minted URI.
    assert config.metadata_base_url == "https://api.example.com/api/rewards/nft"
    assert config.network == "testnet"
    assert config.operator_key_type == "ecdsa"


def test_proof_urls_target_the_configured_network(config: HederaConfig):
    assert config.mirror_node_base_url == "https://testnet.mirrornode.hedera.com"
    assert config.hashscan_transaction_url("0.0.123@1.2") == (
        "https://hashscan.io/testnet/transaction/0.0.123@1.2"
    )
    assert config.hashscan_account_url("0.0.123") == "https://hashscan.io/testnet/account/0.0.123"


def test_mirror_message_decoder_returns_the_signed_json_object():
    encoded = base64.b64encode(json.dumps({"event": "abc", "type": "punch"}).encode()).decode()

    assert _decode_message(encoded) == {"event": "abc", "type": "punch"}


def test_mirror_message_decoder_refuses_non_object_payloads():
    encoded = base64.b64encode(json.dumps(["not", "a", "proof"]).encode()).decode()

    with pytest.raises(ValueError, match="JSON object"):
        _decode_message(encoded)


def test_metadata_key_falls_back_to_the_operator_key(config: HederaConfig):
    assert config.metadata_signing_key == config.operator_key
    with_own_key = HederaConfig.build(
        operator_id="0.0.1",
        operator_key="op",
        metadata_base_url="https://x.dev",
        key_encryption_secret=SECRET,
        metadata_key="meta",
    )
    assert with_own_key is not None
    assert with_own_key.metadata_signing_key == "meta"


def test_card_metadata_uri_versions_by_cycle_and_count(config: HederaConfig):
    card_id = uuid4()
    uri = card_metadata_uri(config, card_id, cycle_number=2, punch_count=3)
    assert uri.decode() == f"{config.metadata_base_url}/{card_id}?v=2-3"
    assert len(uri) <= MAX_METADATA_BYTES
    # A punch has to change the URI, or a cached NFT would never show new progress.
    assert uri != card_metadata_uri(config, card_id, cycle_number=2, punch_count=4)


def test_card_metadata_uri_refuses_to_exceed_the_on_chain_limit():
    config = HederaConfig.build(
        operator_id="0.0.1",
        operator_key="key",
        metadata_base_url=f"https://{'a' * 80}.example.com/rewards/nft",
        key_encryption_secret=SECRET,
    )
    assert config is not None
    with pytest.raises(MetadataTooLargeError):
        card_metadata_uri(config, uuid4(), cycle_number=1, punch_count=0)


def test_custodial_keys_survive_a_round_trip_and_are_not_stored_in_the_clear():
    private_key_der = "302e020100300506032b657004220420" + "ab" * 32
    encrypted = encrypt_private_key(private_key_der, SECRET)
    assert private_key_der not in encrypted
    assert decrypt_private_key(encrypted, SECRET) == private_key_der


def test_ledger_user_ref_is_stable_and_hides_the_user_id():
    user_id = uuid4()
    reference = ledger_user_ref(user_id, SECRET)
    assert reference == ledger_user_ref(user_id, SECRET)
    assert str(user_id) not in reference
    # A different salt yields a different reference, so refs aren't portable between hosts.
    assert reference != ledger_user_ref(user_id, "other-secret")


def test_punch_ledger_message_carries_the_card_but_not_the_user(config: HederaConfig):
    card = fake_card(cycle_number=2, punch_count=1)
    message = _card_message(config, card)
    assert message["v"] == MESSAGE_VERSION
    assert message["card"] == str(card.id)
    assert message["venue"] == str(card.venue_id)
    assert message["cycle"] == 2
    assert message["user"] == ledger_user_ref(card.user_id, SECRET)
    assert str(card.user_id) not in str(message)


async def test_every_ledger_call_is_a_noop_while_unconfigured():
    """The punch cards have to work with no Hedera account anywhere in sight."""
    assert get_hedera_config() is None
    card = fake_card()
    program = SimpleNamespace(venue_id=card.venue_id, hedera_token_id=None, hedera_topic_id=None)

    # Passing no session at all proves none of these reach the database (or the network).
    assert await HederaLedger.ensure_program_ledger(None, program) is program
    assert program.hedera_token_id is None
    assert await HederaLedger.ensure_user_account(None, uuid4()) is None
    assert await HederaLedger.ensure_card_nft(None, card) == (card, False)
    assert card.hedera_nft_serial is None
    assert await HederaLedger.record_punch(None, card) is card
