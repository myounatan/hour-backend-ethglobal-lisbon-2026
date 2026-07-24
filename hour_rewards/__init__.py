"""Punch-card rewards schema for venue loyalty programs.

Importing :mod:`hour_rewards.models` registers five tables with SQLModel's metadata:
``reward_programs``, ``punch_cards``, ``punch_events``, ``reward_redemption_codes`` and
``reward_redemptions``. They link to the host application's ``users``, ``venues``,
``owners`` and ``user_images`` tables -- see "Host contract" in the README.
"""

from hour_rewards.base import TimestampedModel, utc_now, value_enum
from hour_rewards.models import (
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

__version__ = "0.2.0"

__all__ = [
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
    "utc_now",
    "value_enum",
]
