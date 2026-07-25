import enum
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel

from hour_rewards.models.punch_event import PunchEventStatus
from hour_rewards.models.reward_redemption_code import RewardRedemptionCodeStatus


class RewardHistoryEventType(str, enum.Enum):
    PUNCH = "punch"
    REDEEM = "redeem"


class PunchCardSummaryResponse(BaseModel):
    """A user's live progress on one venue's punch card.

    Mirrors ``PunchCardSummary`` in the companion ``hour-rewards-ui`` package field for
    field, so a host's API layer can serialize this directly for the mobile client. The
    ``hedera_*`` fields are null until the card has been minted (see
    :mod:`hour_rewards.hedera`), and are what let a client link a card to its NFT.
    """

    venue_id: UUID
    punches_earned: int
    punches_required: int
    reward_description: str
    hedera_token_id: Optional[str] = None
    hedera_nft_serial: Optional[int] = None
    hedera_explorer_url: Optional[str] = None

    class Config:
        json_encoders = {UUID: str}


class RewardHistoryEventResponse(BaseModel):
    """One entry in a card's timeline -- a punch earned, or a reward claimed.

    Mirrors ``RewardHistoryEvent`` in ``hour-rewards-ui``.
    """

    id: UUID
    type: RewardHistoryEventType
    occurred_at: datetime

    class Config:
        json_encoders = {UUID: str}


class HederaProofResponse(BaseModel):
    """Stored and live Hedera references for a reward history event."""

    network: Optional[str] = None
    topic_id: Optional[str] = None
    topic_sequence_number: Optional[int] = None
    consensus_timestamp: Optional[str] = None
    metadata_transaction_id: Optional[str] = None
    token_id: Optional[str] = None
    nft_serial: Optional[int] = None
    nft_account_id: Optional[str] = None
    topic_url: Optional[str] = None
    nft_url: Optional[str] = None
    metadata_transaction_url: Optional[str] = None
    account_url: Optional[str] = None
    message: Optional[Dict[str, Any]] = None
    mirror_node_url: Optional[str] = None
    message_error: Optional[str] = None


class ZgProofResponse(BaseModel):
    """Stored 0G Compute trace and the result of re-reading its public attestation."""

    request_id: Optional[str] = None
    provider_address: Optional[str] = None
    tee_verified: Optional[bool] = None
    signing_address: Optional[str] = None
    enclave_signer: Optional[str] = None
    tee_verified_live: Optional[bool] = None
    signature: Optional[str] = None
    signature_url: Optional[str] = None
    error: Optional[str] = None


class ReceiptProofResponse(BaseModel):
    dedupe_hash: str
    receipt_identifier: Optional[str] = None
    receipt_date: Optional[datetime] = None
    receipt_total_amount: Optional[float] = None
    ai_confidence_score: Optional[float] = None
    status: PunchEventStatus


class RedemptionProofResponse(BaseModel):
    reward_description: str
    punches_required: int
    cycle_number: int


class RewardProofResponse(BaseModel):
    """Technical evidence for one user-owned punch or redemption history row."""

    id: UUID
    type: RewardHistoryEventType
    occurred_at: datetime
    cycle_number: int
    hedera: HederaProofResponse
    zg: Optional[ZgProofResponse] = None
    receipt: Optional[ReceiptProofResponse] = None
    redemption: Optional[RedemptionProofResponse] = None

    class Config:
        json_encoders = {UUID: str}


class ReceiptSubmissionResponse(BaseModel):
    """What came of photographing a receipt: whether it earned a punch, and who says so.

    ``approved`` is the one field a client needs (it maps onto ``PunchVerificationResult`` in
    ``hour-rewards-ui``); ``reason`` explains a refusal in the vocabulary of
    :mod:`hour_rewards.zg.receipt`, and ``summary`` carries the card's progress *after* the
    submission so a client needn't re-fetch it.

    The ``zg_*`` and ``hedera_*`` fields are what make an approval checkable rather than
    merely asserted: the attested inference that judged the receipt, and where that punch
    landed on the venue's public ledger. Both are null until those layers are configured.
    """

    punch_event_id: Optional[UUID] = None
    status: PunchEventStatus
    approved: bool
    reason: Optional[str] = None
    notes: Optional[str] = None
    confidence: Optional[float] = None
    summary: Optional[PunchCardSummaryResponse] = None

    zg_request_id: Optional[str] = None
    zg_tee_verified: Optional[bool] = None
    hedera_topic_sequence_number: Optional[int] = None
    hedera_consensus_timestamp: Optional[str] = None

    class Config:
        json_encoders = {UUID: str}


class RewardRedemptionCodeResponse(BaseModel):
    """The QR token issued for a completed card, once it is eligible to redeem.

    ``qr_payload`` is the string to put in the QR image -- built by
    :func:`hour_rewards.redemption.build_redemption_payload`, and the only field a client
    needs to display a code. ``expires_in_seconds`` is what to count down (``-1`` for a code
    with no expiry); see :func:`hour_rewards.redemption.seconds_until` for why a duration is
    sent rather than left to a client to work out from ``expires_at``.
    """

    id: UUID
    venue_id: UUID
    punch_card_id: UUID
    cycle_number: int
    token: str
    qr_payload: str
    status: RewardRedemptionCodeStatus
    expires_at: Optional[datetime] = None
    expires_in_seconds: int = -1

    class Config:
        json_encoders = {UUID: str}


class RedemptionScanRequest(BaseModel):
    """What a venue's scanner sends: the scanned string, exactly as the camera read it.

    Parsing it is :func:`hour_rewards.redemption.parse_redemption_payload`'s job, not the
    client's -- a scanner that pulled the token out itself would be a second implementation of
    the format to keep in step.
    """

    qr_payload: str


class RewardRedemptionResponse(BaseModel):
    """A completed, honoured redemption -- the permanent history row."""

    id: UUID
    venue_id: UUID
    user_id: Optional[UUID] = None
    punch_card_id: Optional[UUID] = None
    cycle_number: int
    punches_required: int
    reward_description: str
    redeemed_by_owner_id: Optional[UUID] = None
    created_at: datetime

    class Config:
        json_encoders = {UUID: str}


class RedemptionScanResponse(BaseModel):
    """What came of scanning a code: a reward handed over, or a refusal with a reason.

    Shaped like :class:`ReceiptSubmissionResponse` on purpose. ``approved`` is the one field a
    scanner needs; ``reason`` is from the vocabulary in :mod:`hour_rewards.redemption`, so a
    host can phrase refusals for staff; ``reward_description`` says what to actually give out.
    """

    approved: bool
    reason: Optional[str] = None
    reward_description: Optional[str] = None
    redemption: Optional[RewardRedemptionResponse] = None

    class Config:
        json_encoders = {UUID: str}
