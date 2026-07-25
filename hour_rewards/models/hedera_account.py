from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, UniqueConstraint

from hour_rewards.base import TimestampedModel


class HederaAccount(TimestampedModel, table=True):
    """The Hedera account holding one user's punch-card NFTs, custodied by the host.

    Punch-card users sign in with Google or Apple and bring no wallet, so an account is
    created for them the first time they earn a punch at an opted-in venue, and its key is
    stored here encrypted with the host's ``key_encryption_secret``. Cards are genuinely
    owned by (and visible under) this account on HashScan; the keys are simply held on the
    user's behalf. Deliberately no relationship back to the host's ``User``, so adopting
    the Hedera layer needs no change to the host's own models.
    """

    __tablename__ = "hedera_accounts"
    __table_args__ = (UniqueConstraint("user_id", name="uq_hedera_accounts_user_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")

    network: str = Field(max_length=32)
    account_id: str = Field(max_length=64)
    public_key: str = Field(max_length=256)
    # DER-encoded and Fernet-encrypted; see hour_rewards.hedera.keys.
    encrypted_private_key: str


class HederaAccountResponse(SQLModel):
    """The custodial account, without anything key-related."""

    user_id: UUID
    network: str
    account_id: str

    @classmethod
    def from_account(cls, account: Optional[HederaAccount]) -> Optional["HederaAccountResponse"]:
        if account is None:
            return None
        return cls(user_id=account.user_id, network=account.network, account_id=account.account_id)
