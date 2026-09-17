"""Redis cache for completed analyses.

Key layout (db 0):
    video:{instagram_id}   -> JSON analysis payload, TTL 24h (SETEX)
    stats:cache:hits       -> hit counter (observability)
    stats:cache:misses     -> miss counter
    ratelimit:*            -> rate limiter windows (see app.middleware)
    dlq:*                  -> dead-letter queue for failed jobs (see app.tasks)

Cache failures never break a request: reads degrade to a miss, writes to a no-op.
Connection pools are created once per process and reused (never per request).
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any

import redis
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "video:"
STATS_HITS_KEY = "stats:cache:hits"
STATS_MISSES_KEY = "stats:cache:misses"


def cache_key(instagram_id: str) -> str:
    return f"{KEY_PREFIX}{instagram_id}"


def serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def deserialize(raw: str | bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("cache_payload_corrupted")
        return None
    return value if isinstance(value, dict) else None


def create_async_redis(settings: Settings | None = None) -> aioredis.Redis:
    settings = settings or get_settings()
    pool = aioredis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_pool_max,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
        health_check_interval=30,
        decode_responses=True,
    )
    return aioredis.Redis(connection_pool=pool)


@lru_cache
def get_sync_redis() -> redis.Redis:
    """Blocking client for the worker (lazily created per process)."""
    settings = get_settings()
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_pool_max,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
        health_check_interval=30,
        decode_responses=True,
    )
    return redis.Redis(connection_pool=pool)


class CacheService:
    """Async cache used by the API."""

    def __init__(self, client: aioredis.Redis, ttl_seconds: int | None = None) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds or get_settings().cache_ttl_seconds

    async def get(self, instagram_id: str, *, count_stats: bool = True) -> dict[str, Any] | None:
        """`count_stats=False` for reads the app makes on its own behalf, which
        would otherwise inflate the hit ratio reported by /api/health."""
        try:
            raw = await self.client.get(cache_key(instagram_id))
            data = deserialize(raw)
            if count_stats:
                await self.client.incr(STATS_HITS_KEY if data is not None else STATS_MISSES_KEY)
        except RedisError as exc:
            logger.warning("cache_get_failed", extra={"error": type(exc).__name__})
            return None
        logger.debug("cache_hit" if data is not None else "cache_miss", extra={"instagram_id": instagram_id})
        return data

    async def set(self, instagram_id: str, data: dict[str, Any], ttl_seconds: int | None = None) -> bool:
        try:
            await self.client.setex(cache_key(instagram_id), ttl_seconds or self.ttl_seconds, serialize(data))
            return True
        except RedisError as exc:
            logger.warning("cache_set_failed", extra={"error": type(exc).__name__})
            return False

    async def delete(self, instagram_id: str) -> bool:
        try:
            return bool(await self.client.delete(cache_key(instagram_id)))
        except RedisError as exc:
            logger.warning("cache_delete_failed", extra={"error": type(exc).__name__})
            return False

    async def list(self, limit: int = 100) -> list[str]:
        """Instagram ids currently cached (SCAN, never KEYS, to avoid blocking Redis)."""
        ids: list[str] = []
        try:
            async for key in self.client.scan_iter(match=f"{KEY_PREFIX}*", count=100):
                ids.append(str(key)[len(KEY_PREFIX):])
                if len(ids) >= limit:
                    break
        except RedisError as exc:
            logger.warning("cache_list_failed", extra={"error": type(exc).__name__})
        return ids

    async def ttl(self, instagram_id: str) -> int | None:
        try:
            value = await self.client.ttl(cache_key(instagram_id))
        except RedisError:
            return None
        return int(value) if value is not None and value >= 0 else None

    async def ping(self, timeout: float = 2.0) -> bool:
        try:
            return bool(await asyncio.wait_for(self.client.ping(), timeout=timeout))
        except (TimeoutError, RedisError, OSError) as exc:
            logger.warning("redis_health_check_failed", extra={"error": type(exc).__name__})
            return False

    async def stats(self) -> dict[str, Any]:
        try:
            hits_raw, misses_raw = await self.client.mget(STATS_HITS_KEY, STATS_MISSES_KEY)
        except RedisError:
            return {"hits": 0, "misses": 0, "hit_ratio": None}
        hits, misses = int(hits_raw or 0), int(misses_raw or 0)
        total = hits + misses
        return {"hits": hits, "misses": misses, "hit_ratio": round(hits / total, 4) if total else None}


class SyncCacheService:
    """Blocking cache used by the Celery worker."""

    def __init__(self, client: redis.Redis | None = None, ttl_seconds: int | None = None) -> None:
        self.client = client or get_sync_redis()
        self.ttl_seconds = ttl_seconds or get_settings().cache_ttl_seconds

    def get(self, instagram_id: str) -> dict[str, Any] | None:
        try:
            return deserialize(self.client.get(cache_key(instagram_id)))  # type: ignore[arg-type]
        except RedisError as exc:
            logger.warning("cache_get_failed", extra={"error": type(exc).__name__})
            return None

    def set(self, instagram_id: str, data: dict[str, Any], ttl_seconds: int | None = None) -> bool:
        try:
            self.client.setex(cache_key(instagram_id), ttl_seconds or self.ttl_seconds, serialize(data))
            return True
        except RedisError as exc:
            logger.warning("cache_set_failed", extra={"error": type(exc).__name__})
            return False

    def delete(self, instagram_id: str) -> bool:
        try:
            return bool(self.client.delete(cache_key(instagram_id)))
        except RedisError as exc:
            logger.warning("cache_delete_failed", extra={"error": type(exc).__name__})
            return False
