"""Credentials and tuning for the 0G verification layer, handed in by the host application.

Same contract as :mod:`hour_rewards.hedera.config`: this package never reads the host's
environment, so a host builds a :class:`ZGConfig` from its own settings and calls
:func:`configure_zg` once at startup. Until it does, :func:`get_zg_config` returns ``None``
and every submitted receipt is refused -- unlike the ledger, an absent verifier cannot be a
no-op, because a punch nobody checked is not a punch.
"""

from dataclasses import dataclass
from typing import Optional

# 0G Compute is reached through an OpenAI-compatible gateway: provider discovery, TEE
# attestation and on-chain billing happen behind it, so a host needs an API key rather than a
# wallet (https://docs.0g.ai/developer-hub/building-on-0g/compute-network). An `app-sk-` key is
# issued *for one gateway*, and the number in the host differs per app, so a host with its own
# key should pass the base URL that came with it rather than rely on this default.
DEFAULT_BASE_URL = "https://compute-network-6.integratenetwork.work/v1/proxy"

DEFAULT_MODEL = "qwen/qwen2.5-omni-7b"

# Above this, a receipt the model approved becomes a punch; below it the submission is
# refused as `low_confidence`, so a hesitant read asks for a better photo rather than
# handing out a punch on a maybe.
DEFAULT_MIN_CONFIDENCE = 0.75

DEFAULT_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class ZGConfig:
    """Everything the verifier needs: which gateway to call, as whom, and how sure is sure.

    ``verify_tee`` costs two extra public GETs per verification -- 0G's signed receipt for the
    run, and the serving node's enclave quote -- which together are what make a punch auditable
    rather than merely logged. See :mod:`hour_rewards.zg.verifier` for what is kept of them.
    """

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    verify_tee: bool = True
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def build(cls, api_key: Optional[str], **kwargs: object) -> Optional["ZGConfig"]:
        """The config, or ``None`` when there is no API key to call the Router with.

        Lets a host pass its optional settings straight through, exactly as
        :meth:`hour_rewards.hedera.HederaConfig.build` does.
        """
        if not api_key:
            return None
        # Unset optionals fall back to the defaults above rather than overwriting them
        # with None, so a host can pass every setting through unconditionally.
        overrides = {key: value for key, value in kwargs.items() if value is not None}
        return cls(api_key=api_key, **overrides)  # type: ignore[arg-type]


_config: Optional[ZGConfig] = None


def configure_zg(config: Optional[ZGConfig]) -> None:
    """Install (or with ``None``, clear) the config every verification reads."""
    global _config
    _config = config


def get_zg_config() -> Optional[ZGConfig]:
    return _config
