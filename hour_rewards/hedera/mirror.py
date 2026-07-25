"""Read public HCS proof data from a Hedera mirror node."""

import base64
import json
import logging
from typing import Any, Dict, Optional

from hour_rewards.hedera.config import HederaConfig

logger = logging.getLogger("hour_rewards.hedera")


async def fetch_topic_message(
    config: HederaConfig, topic_id: str, sequence_number: int
) -> Optional[Dict[str, Any]]:
    """Return one HCS message as the network stored it, or ``None`` when unreadable."""
    url = (
        f"{config.mirror_node_base_url}/api/v1/topics/{topic_id}/messages"
        f"/{sequence_number}"
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        message = _decode_message(payload.get("message"))
        return {
            "message": message,
            "consensus_timestamp": payload.get("consensus_timestamp"),
            "payer_account_id": payload.get("payer_account_id"),
            "running_hash": payload.get("running_hash"),
            "mirror_node_url": url,
        }
    except Exception as error:
        logger.warning(
            "Could not read HCS message %s/%s from mirror node: %s",
            topic_id,
            sequence_number,
            error,
        )
        return None


def _decode_message(encoded_message: object) -> Dict[str, Any]:
    """Decode the base64 JSON payload returned by the mirror node."""
    if not isinstance(encoded_message, str):
        raise ValueError("Mirror-node response did not contain a topic message.")
    decoded = base64.b64decode(encoded_message, validate=True).decode("utf-8")
    message = json.loads(decoded)
    if not isinstance(message, dict):
        raise ValueError("HCS message was not a JSON object.")
    return message
