"""The `hiero_sdk_python` client, key parsing, and the bridge from its sync API to async.

The SDK's ``execute()`` blocks on gRPC until consensus, so every call from the async
service layer goes through :func:`in_thread`. Clients hold gRPC channels, so one is cached
per network+operator rather than built per transaction.
"""

import asyncio
import threading
from typing import Callable, Dict, Tuple, TypeVar

from hiero_sdk_python import AccountId, Client, Network, PrivateKey

from hour_rewards.hedera.config import HederaConfig

T = TypeVar("T")

# Testnet consensus is sub-second, but a submission that is retrying node-side should fail
# the request rather than hold a punch or redemption open indefinitely.
TRANSACTION_TIMEOUT_SECONDS = 20

_clients: Dict[Tuple[str, str], Client] = {}
_clients_lock = threading.Lock()


def parse_private_key(value: str, key_type: str = "ecdsa") -> PrivateKey:
    """Parse a key string, preferring the algorithm a DER encoding declares for itself.

    ``PrivateKey.from_string`` reads a bare 32-byte hex key as Ed25519, which silently
    produces the wrong key for the ECDSA credentials the Hedera portal hands out, so the
    algorithm is explicit here.
    """
    text = value.strip().removeprefix("0x")
    if text.startswith("302"):
        return PrivateKey.from_string_der(text)
    if key_type.lower() == "ed25519":
        return PrivateKey.from_string_ed25519(text)
    return PrivateKey.from_string_ecdsa(text)


def get_client(config: HederaConfig) -> Client:
    cache_key = (config.network, config.operator_id)
    with _clients_lock:
        client = _clients.get(cache_key)
        if client is None:
            client = Client(Network(network=config.network))
            client.set_operator(
                AccountId.from_string(config.operator_id),
                parse_private_key(config.operator_key, config.operator_key_type),
            )
            _clients[cache_key] = client
        return client


def close_clients() -> None:
    """Close every cached client's channels; hosts call this on shutdown."""
    with _clients_lock:
        for client in _clients.values():
            client.close()
        _clients.clear()


async def in_thread(call: Callable[[], T]) -> T:
    """Run one blocking SDK call off the event loop."""
    return await asyncio.to_thread(call)
