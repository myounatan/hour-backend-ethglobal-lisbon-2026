"""Punch cards on Hedera, using only the native token, consensus and account services.

A venue that opts in gets its own HTS NFT collection and an HCS topic; each user's card is
one serial in that collection, held by a custodial account and re-pointed at fresh HIP-412
metadata as punches land. See :mod:`hour_rewards.hedera.ledger` for the full shape of it,
and the README's "Hedera" section for what a host has to provide.

Nothing here runs until a host calls :func:`configure_hedera`, and nothing here can fail a
punch: the database stays the source of truth and ledger errors are logged, not raised.
"""

from hour_rewards.hedera.config import (
    MAX_METADATA_BYTES,
    HederaConfig,
    configure_hedera,
    get_hedera_config,
)
from hour_rewards.hedera.ledger import DEFAULT_RECONCILE_LIMIT, HederaLedger
from hour_rewards.hedera.metadata import (
    MetadataTooLargeError,
    build_card_metadata,
    card_metadata_uri,
)


def close_hedera_clients() -> None:
    """Release the cached gRPC clients; hosts call this on shutdown.

    Imports the client module only if the layer was ever configured, so a host without the
    ``hedera`` extra installed can still call this unconditionally.
    """
    if get_hedera_config() is None:
        return
    from hour_rewards.hedera.client import close_clients

    close_clients()


__all__ = [
    "DEFAULT_RECONCILE_LIMIT",
    "HederaConfig",
    "HederaLedger",
    "MAX_METADATA_BYTES",
    "MetadataTooLargeError",
    "build_card_metadata",
    "card_metadata_uri",
    "close_hedera_clients",
    "configure_hedera",
    "get_hedera_config",
]
