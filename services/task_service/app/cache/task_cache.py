import hashlib
import json
import logging
from typing import Any

from redis.exceptions import RedisError

from ..api.schemas import CurrentUser

logger = logging.getLogger("task-service.cache")


def _hash_parts(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_task_item_cache_key(*, current_user: CurrentUser, task_id: str) -> str:
    user_context = {
        "user_id": current_user.user_id,
        "roles": sorted(current_user.roles),
        "team_ids": sorted(current_user.team_ids),
    }
    return f"tasks:item:{_hash_parts(user_context)}:{task_id}"


def get_cached_json(*, redis_client: Any | None, key: str) -> dict[str, Any] | None:
    if redis_client is None:
        logger.info("cache_miss key=%s reason=redis_disabled", key)
        return None

    try:
        cached = redis_client.get(key)
    except RedisError as exc:
        logger.warning("cache_miss key=%s reason=redis_error error=%s", key, exc)
        return None

    if cached is None:
        logger.info("cache_miss key=%s", key)
        return None

    logger.info("cache_hit key=%s", key)
    return json.loads(cached)


def set_cached_json(*, redis_client: Any | None, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    if redis_client is None or ttl_seconds <= 0:
        return

    try:
        redis_client.setex(key, ttl_seconds, json.dumps(value, default=str))
    except RedisError as exc:
        logger.warning("cache_set_failed key=%s error=%s", key, exc)

def invalidate_task_cache(redis_client: Any | None) -> int:
    if redis_client is None:
        logger.info("cache_invalidate prefix=tasks:* count=0 reason=redis_disabled")
        return 0

    try:
        keys = list(redis_client.scan_iter(match="tasks:*", count=100))
        if not keys:
            logger.info("cache_invalidate prefix=tasks:* count=0")
            return 0
        deleted = int(redis_client.delete(*keys))
    except RedisError as exc:
        logger.warning("cache_invalidate_failed prefix=tasks:* error=%s", exc)
        return 0

    logger.info("cache_invalidate prefix=tasks:* count=%s", deleted)
    return deleted
