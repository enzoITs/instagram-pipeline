from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from sqlalchemy import select

from app.analyzer.models import AnalysisCache
from app.analyzer.repository import delete_cache_backup, get_cache_backup, upsert_cache_backup
from app.analyzer.services.cache_service import (
    STATS_HITS_KEY,
    CacheService,
    SyncCacheService,
    cache_key,
    create_async_redis,
    deserialize,
    get_sync_redis,
    serialize,
)
from app.database import dispose_async_engine, get_async_sessionmaker, session_scope

PAYLOAD = {"video_id": "abc", "metrics": {"views": 10}, "caption": "Acentuação 😍"}


@pytest.fixture
async def cache(async_redis):
    return CacheService(async_redis, ttl_seconds=86400)


def test_serialization_roundtrip():
    raw = serialize(PAYLOAD)
    assert "😍" in raw  # no ASCII escaping: smaller payloads
    assert deserialize(raw) == PAYLOAD
    assert deserialize(None) is None
    assert deserialize("{broken") is None
    assert deserialize("[1, 2]") is None
    assert cache_key("C8x") == "video:C8x"


async def test_set_get_delete_with_ttl(cache, async_redis):
    assert await cache.get("C8x") is None
    assert await cache.set("C8x", PAYLOAD)
    assert await cache.get("C8x") == PAYLOAD
    ttl = await cache.ttl("C8x")
    assert 86000 < ttl <= 86400
    assert await cache.list() == ["C8x"]
    assert await cache.delete("C8x") is True
    assert await cache.delete("C8x") is False
    assert await cache.ttl("C8x") is None
    stats = await cache.stats()
    assert stats == {"hits": 1, "misses": 1, "hit_ratio": 0.5}


async def test_list_is_bounded(cache):
    for index in range(5):
        await cache.set(f"id{index}", PAYLOAD)
    assert len(await cache.list(limit=3)) == 3


async def test_stats_without_traffic(cache):
    assert await cache.stats() == {"hits": 0, "misses": 0, "hit_ratio": None}


async def test_corrupted_entry_counts_as_miss(cache, async_redis):
    await async_redis.set(cache_key("bad"), "{not json")
    assert await cache.get("bad") is None
    assert int(await async_redis.get("stats:cache:misses")) == 1
    assert await async_redis.get(STATS_HITS_KEY) is None


async def test_redis_outage_degrades_gracefully(fake_server):
    fake_server.connected = False
    client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    cache = CacheService(client, ttl_seconds=60)
    assert await cache.get("x") is None
    assert await cache.set("x", PAYLOAD) is False
    assert await cache.delete("x") is False
    assert await cache.list() == []
    assert await cache.ttl("x") is None
    assert await cache.ping(timeout=0.5) is False
    assert await cache.stats() == {"hits": 0, "misses": 0, "hit_ratio": None}


async def test_ping(cache):
    assert await cache.ping() is True


def test_sync_cache(sync_redis, fake_server):
    cache = SyncCacheService(client=sync_redis, ttl_seconds=120)
    assert cache.set("C8x", PAYLOAD)
    assert cache.get("C8x") == PAYLOAD
    assert 0 < sync_redis.ttl(cache_key("C8x")) <= 120
    assert cache.delete("C8x")

    fake_server.connected = False
    assert cache.set("C8x", PAYLOAD) is False
    assert cache.get("C8x") is None
    assert cache.delete("C8x") is False


async def test_client_factories_build_pools(settings):
    client = create_async_redis(settings)
    assert client.connection_pool.max_connections == settings.redis_pool_max
    await client.aclose()
    get_sync_redis.cache_clear()
    sync_client = get_sync_redis()
    assert sync_client is get_sync_redis()  # pooled client reused, never created per request
    assert sync_client.connection_pool.max_connections == settings.redis_pool_max
    get_sync_redis.cache_clear()


# --------------------------------------------------------------------------- PostgreSQL backup table
async def test_cache_backup_roundtrip_and_expiry(db_engine):
    with session_scope() as session:
        upsert_cache_backup(session, "C8x", PAYLOAD, ttl_seconds=3600)
    with session_scope() as session:  # upsert updates in place
        upsert_cache_backup(session, "C8x", {**PAYLOAD, "caption": "v2"}, ttl_seconds=7200)
        rows = session.scalars(select(AnalysisCache)).all()
        assert len(rows) == 1 and rows[0].ttl_minutes == 120

    async with get_async_sessionmaker()() as session:
        assert (await get_cache_backup(session, "C8x"))["caption"] == "v2"
        assert await get_cache_backup(session, "missing") is None

    with session_scope() as session:
        session.scalars(select(AnalysisCache)).one().expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with get_async_sessionmaker()() as session:
        assert await get_cache_backup(session, "C8x") is None
        await delete_cache_backup(session, "C8x")
        await session.commit()
    with session_scope() as session:
        assert session.scalars(select(AnalysisCache)).first() is None
    await dispose_async_engine()
