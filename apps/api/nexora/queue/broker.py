"""Redis connection used strictly for queue signalling and short-lived caching.

Durable job state lives in PostgreSQL (``jobs`` table). Redis carries only the wake-up
notification and ephemeral locks, so a Redis flush delays work but never loses it.
"""

from __future__ import annotations

import time
from typing import Any

import redis

from nexora.config import settings
from nexora.db.models.enums import ComponentStatus

READY_LIST = "nexora:jobs:ready"
CANCEL_CHANNEL = "nexora:jobs:cancel"

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=15,
            health_check_interval=30,
        )
    return _client


def reset_redis() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # pragma: no cover - close is best effort
            pass
    _client = None


def notify_ready(job_id: str) -> bool:
    """Signal a waiting worker. Returns False when Redis is down (the DB sweep covers it)."""
    try:
        get_redis().lpush(READY_LIST, job_id)
        return True
    except redis.RedisError:
        return False


def wait_for_ready(timeout: int = 5) -> str | None:
    try:
        item = get_redis().brpop(READY_LIST, timeout=timeout)
    except redis.RedisError:
        time.sleep(min(timeout, 5))
        return None
    return item[1] if item else None


def queue_depth() -> int | None:
    try:
        return int(get_redis().llen(READY_LIST))
    except redis.RedisError:
        return None


def queue_health() -> Any:
    from nexora.services.health import ComponentHealth

    started = time.perf_counter()
    try:
        client = get_redis()
        client.ping()
        info = client.info("server")
        depth = int(client.llen(READY_LIST))
    except redis.RedisError as exc:
        return ComponentHealth(
            name="queue",
            status=ComponentStatus.UNHEALTHY,
            detail=f"Redis is not reachable: {exc}",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    return ComponentHealth(
        name="queue",
        status=ComponentStatus.HEALTHY,
        detail=f"Redis {info.get('redis_version', 'unknown')}",
        latency_ms=(time.perf_counter() - started) * 1000,
        metadata={"ready_depth": depth, "redis_version": info.get("redis_version")},
    )
