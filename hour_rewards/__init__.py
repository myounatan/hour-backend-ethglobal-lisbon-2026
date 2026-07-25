"""Punch-card rewards schema for venue loyalty programs.

Importing :mod:`hour_rewards.models` registers six tables with SQLModel's metadata:
``reward_programs``, ``punch_cards``, ``punch_events``, ``reward_redemption_codes``,
``reward_redemptions`` and ``hedera_accounts``. They link to the host application's
``users``, ``venues``, ``owners`` and ``user_images`` tables -- see "Host contract" in the
README.

Two optional layers hang off that schema, each dormant until a host configures it:
:mod:`hour_rewards.zg` decides whether a photographed receipt earns a punch, by judging its
text on 0G Compute and keeping the attestation for the run that decided; and
:mod:`hour_rewards.hedera` mints each user's card as an HTS NFT in its venue's collection and
logs every punch -- with that attestation attached -- to an HCS topic.

A punch therefore starts from an uploaded photo, and :mod:`hour_rewards.receipt_photo` takes it
from there: a host's upload endpoint hands over the bytes and this package validates, reads
(through the host's own OCR) and judges them.
"""

from hour_rewards.base import LedgerProofModel, TimestampedModel, utc_now, value_enum
from hour_rewards.hedera import (
    HederaConfig,
    HederaLedger,
    build_card_metadata,
    close_hedera_clients,
    configure_hedera,
    get_hedera_config,
)
from hour_rewards.models import (
    HederaAccount,
    HederaAccountResponse,
    PunchCard,
    PunchCardBase,
    PunchCardSummaryResponse,
    PunchEvent,
    PunchEventStatus,
    ReceiptSubmissionResponse,
    RewardHistoryEventResponse,
    RewardHistoryEventType,
    RewardProgram,
    RewardProgramBase,
    RewardProgramCreate,
    RewardProgramResponse,
    RewardProgramUpdateRequest,
    RewardRedemption,
    RewardRedemptionCode,
    RewardRedemptionCodeResponse,
    RewardRedemptionCodeStatus,
    RewardRedemptionResponse,
)
from hour_rewards.receipt_photo import (
    MAX_RECEIPT_IMAGE_BYTES,
    ReceiptImageError,
    ReceiptImageTooLargeError,
    ReceiptReader,
    ReceiptScanError,
    UnsupportedReceiptImageError,
    configure_receipt_reader,
    get_receipt_reader,
    submit_receipt_photo,
    validate_receipt_image,
)
from hour_rewards.service import RewardService, RewardServiceError
from hour_rewards.zg import ReceiptVerdict, ZGConfig, configure_zg, get_zg_config, verify_receipt

__version__ = "0.5.0"

__all__ = [
    "HederaAccount",
    "HederaAccountResponse",
    "HederaConfig",
    "HederaLedger",
    "LedgerProofModel",
    "MAX_RECEIPT_IMAGE_BYTES",
    "PunchCard",
    "PunchCardBase",
    "PunchCardSummaryResponse",
    "PunchEvent",
    "PunchEventStatus",
    "ReceiptImageError",
    "ReceiptImageTooLargeError",
    "ReceiptReader",
    "ReceiptScanError",
    "ReceiptSubmissionResponse",
    "ReceiptVerdict",
    "RewardHistoryEventResponse",
    "RewardHistoryEventType",
    "RewardProgram",
    "RewardProgramBase",
    "RewardProgramCreate",
    "RewardProgramResponse",
    "RewardProgramUpdateRequest",
    "RewardRedemption",
    "RewardRedemptionCode",
    "RewardRedemptionCodeResponse",
    "RewardRedemptionCodeStatus",
    "RewardRedemptionResponse",
    "RewardService",
    "RewardServiceError",
    "TimestampedModel",
    "UnsupportedReceiptImageError",
    "ZGConfig",
    "__version__",
    "build_card_metadata",
    "close_hedera_clients",
    "configure_hedera",
    "configure_receipt_reader",
    "configure_zg",
    "get_hedera_config",
    "get_receipt_reader",
    "get_zg_config",
    "submit_receipt_photo",
    "utc_now",
    "validate_receipt_image",
    "value_enum",
    "verify_receipt",
]
