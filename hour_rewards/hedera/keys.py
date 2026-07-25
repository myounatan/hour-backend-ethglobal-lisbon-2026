"""Encryption for the custodial account keys this package stores, and HCS user hashing.

Punch-card users sign in with Google or Apple and have no wallet of their own, so the
account holding their card NFTs is created and held here (see the "Custody" note in the
README). Its private key is stored encrypted with a secret only the host knows, and the
user identifiers published to the public punch ledger are salted hashes rather than the
host's own UUIDs.
"""

import base64
import hashlib
from uuid import UUID

from cryptography.fernet import Fernet

# 16 bytes of SHA-256 is enough to correlate a user's punches on the topic without
# publishing an identifier that maps back to a host row.
LEDGER_USER_REF_LENGTH = 16


def _fernet(secret: str) -> Fernet:
    """Fernet keyed by the host's secret, which is arbitrary text rather than a 32-byte key."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_private_key(private_key_der: str, secret: str) -> str:
    return _fernet(secret).encrypt(private_key_der.encode()).decode()


def decrypt_private_key(encrypted_private_key: str, secret: str) -> str:
    return _fernet(secret).decrypt(encrypted_private_key.encode()).decode()


def ledger_user_ref(user_id: UUID, secret: str) -> str:
    """A stable, salted stand-in for a user id, safe to publish on a public topic."""
    digest = hashlib.sha256(f"{user_id}:{secret}".encode()).hexdigest()
    return digest[:LEDGER_USER_REF_LENGTH]
