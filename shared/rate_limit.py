import logging
from dataclasses import dataclass
from typing import Any

from redis.exceptions import RedisError

logger = logging.getLogger("rate-limit")


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int
    remaining: int | None = None


def check_fixed_window_rate_limit(
    *,
    redis_client: Any | None,
    key: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    if redis_client is None or limit <= 0 or window_seconds <= 0:
        return RateLimitResult(allowed=True, retry_after_seconds=0, remaining=None)

    try:
        current = int(redis_client.incr(key))
        if current == 1:
            redis_client.expire(key, window_seconds)
        ttl = int(redis_client.ttl(key))
    except RedisError as exc:
        logger.warning("rate_limit_degraded key=%s error=%s", key, exc)
        return RateLimitResult(allowed=True, retry_after_seconds=0, remaining=None)

    retry_after = ttl if ttl > 0 else window_seconds
    remaining = max(limit - current, 0)
    if current > limit:
        return RateLimitResult(allowed=False, retry_after_seconds=retry_after, remaining=0)
    return RateLimitResult(allowed=True, retry_after_seconds=retry_after, remaining=remaining)
