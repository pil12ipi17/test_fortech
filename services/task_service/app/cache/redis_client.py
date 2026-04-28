import logging
from functools import lru_cache

from redis import Redis

from ..core.config import Settings

logger = logging.getLogger("task-service.redis")


@lru_cache
def _build_redis_client(redis_url: str, socket_timeout_seconds: float) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=socket_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
    )


def get_redis_client(settings: Settings) -> Redis | None:
    if not settings.redis_url:
        logger.info("redis_disabled service=task-service")
        return None
    return _build_redis_client(settings.redis_url, settings.redis_socket_timeout_seconds)
