"""Credentials and tuning for the Hedera layer, handed in by the host application.

This package never reads the host's environment: a host builds a :class:`HederaConfig`
from its own settings and calls :func:`configure_hedera` once at startup. Until it does,
:func:`get_hedera_config` returns ``None`` and every ledger call is a no-op, which is what
keeps the punch-card tables (and their tests) usable with no Hedera credentials at all.
"""

from dataclasses import dataclass
from typing import Optional

DEFAULT_NETWORK = "testnet"

# HIP-904 unlimited automatic token associations, so a custodial account can be handed a
# punch card from any venue without a prior TokenAssociateTransaction per collection.
UNLIMITED_AUTO_ASSOCIATIONS = -1

# HIP-657 caps an NFT's metadata field at 100 bytes, which is why that field holds a URI
# pointing at `metadata_base_url` rather than the HIP-412 JSON itself.
MAX_METADATA_BYTES = 100

# Every punch card in a venue's collection is one serial of the same NFT class.
TOKEN_SYMBOL = "PUNCH"


@dataclass(frozen=True)
class HederaConfig:
    """Everything the ledger needs: an operator to pay and sign, and where metadata lives.

    ``operator_key`` doubles as the admin and supply key for every venue collection this
    package creates, and as the metadata key unless ``metadata_key`` is set separately.
    ``key_encryption_secret`` encrypts the custodial account keys this package stores (see
    :mod:`hour_rewards.hedera.keys`) and salts the user identifiers it publishes to HCS.
    """

    operator_id: str
    operator_key: str
    metadata_base_url: str
    key_encryption_secret: str
    network: str = DEFAULT_NETWORK
    operator_key_type: str = "ecdsa"
    metadata_key: Optional[str] = None
    max_auto_associations: int = UNLIMITED_AUTO_ASSOCIATIONS
    initial_account_balance: int = 0

    @classmethod
    def build(
        cls,
        operator_id: Optional[str],
        operator_key: Optional[str],
        metadata_base_url: Optional[str],
        key_encryption_secret: Optional[str],
        **kwargs: object,
    ) -> Optional["HederaConfig"]:
        """The config, or ``None`` when any credential is missing.

        Lets a host pass its optional settings straight through -- an environment with no
        Hedera credentials configured disables the layer instead of failing at startup.
        """
        if not (operator_id and operator_key and metadata_base_url and key_encryption_secret):
            return None
        # Unset optionals fall back to the defaults above rather than overwriting them
        # with None, so a host can pass every setting through unconditionally.
        overrides = {key: value for key, value in kwargs.items() if value is not None}
        return cls(
            operator_id=operator_id,
            operator_key=operator_key,
            metadata_base_url=metadata_base_url.rstrip("/"),
            key_encryption_secret=key_encryption_secret,
            **overrides,  # type: ignore[arg-type]
        )

    @property
    def metadata_signing_key(self) -> str:
        """The key that signs ``TokenUpdateNftsTransaction``; the operator's by default."""
        return self.metadata_key or self.operator_key

    def hashscan_nft_url(self, token_id: str, serial_number: int) -> str:
        return f"https://hashscan.io/{self.network}/token/{token_id}/{serial_number}"

    def hashscan_topic_url(self, topic_id: str) -> str:
        return f"https://hashscan.io/{self.network}/topic/{topic_id}"


_config: Optional[HederaConfig] = None


def configure_hedera(config: Optional[HederaConfig]) -> None:
    """Install (or with ``None``, clear) the config every ledger call reads."""
    global _config
    _config = config


def get_hedera_config() -> Optional[HederaConfig]:
    return _config
