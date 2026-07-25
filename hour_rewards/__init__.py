"""Punch-card rewards schema for venue loyalty programs.

Importing :mod:`hour_rewards.models` registers six tables with SQLModel's metadata:
``reward_programs``, ``punch_cards``, ``punch_events``, ``reward_redemption_codes``,
``reward_redemptions`` and ``hedera_accounts``. They link to the host application's
``users``, ``venues``, ``owners`` and ``user_images`` tables -- see "Host contract" in the
README.

The optional Hedera layer (:mod:`hour_rewards.hedera`) mints each user's card as an HTS NFT
in its venue's collection and logs every punch to an HCS topic. It stays dormant until a
host calls :func:`hour_rewards.hedera.configure_hedera`.
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
from hour_rewards.service import RewardService, RewardServiceError

__version__ = "0.3.0"

__all__ = [
    "HederaAccount",
    "HederaAccountResponse",
    "HederaConfig",
    "HederaLedger",
    "LedgerProofModel",
    "PunchCard",
    "PunchCardBase",
    "PunchCardSummaryResponse",
    "PunchEvent",
    "PunchEventStatus",
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
    "__version__",
    "build_card_metadata",
    "close_hedera_clients",
    "configure_hedera",
    "get_hedera_config",
    "utc_now",
    "value_enum",
]
