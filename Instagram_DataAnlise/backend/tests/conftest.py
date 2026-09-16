"""Shared fixtures.

By default the suite runs on SQLite + fakeredis (no services needed). Point
TEST_DATABASE_URL at PostgreSQL to run the exact same tests against the real database:

    TEST_DATABASE_URL=postgresql+asyncpg://analyzer:analyzer@localhost:5432/analyzer_test pytest
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import fakeredis
import httpx
import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="analyzer-tests-")
os.environ.update(
    {
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DIR}/test.db"),
        "ENVIRONMENT": "test",
        "TASK_BACKEND": "inline",
        "SCRAPER_MODE": "mock",
        "NLP_BACKEND": "lexicon",
        "OPENAI_API_KEY": "",
        "APIFY_TOKEN": "",
        "INSTAGRAM_USERNAME": "",
        "INSTAGRAM_SESSIONFILE": "",
        "LOG_FORMAT": "text",
        "LOG_LEVEL": "WARNING",
        "RATE_LIMIT_PER_MINUTE": "10",
        "TRUST_PROXY_HEADERS": "false",
        "CORS_ORIGINS": "http://localhost:5173,http://localhost:3000",
        "REDIS_URL": "redis://127.0.0.1:6399/0",
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "CACHE_TTL_SECONDS": "86400",
    }
)

from app.config import get_settings  # noqa: E402
from app.database import dispose_async_engine, get_sync_engine, reset_sync_engine, session_scope  # noqa: E402
from app.models import Base, Video, VideoStatus  # noqa: E402


@pytest.fixture
def settings() -> Any:
    return get_settings()


@pytest.fixture
def db_engine() -> Iterator[Any]:
    engine = get_sync_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    reset_sync_engine()
    Base.metadata.drop_all(get_sync_engine())


@pytest.fixture
def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def sync_redis(fake_server: fakeredis.FakeServer, monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
    import app.pipeline
    import app.services.cache_service

    monkeypatch.setattr(app.services.cache_service, "get_sync_redis", lambda: client)
    monkeypatch.setattr(app.pipeline, "get_sync_redis", lambda: client)
    return client


@pytest.fixture
async def async_redis(fake_server: fakeredis.FakeServer) -> AsyncIterator[Any]:
    client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def client(db_engine: Any, async_redis: Any, sync_redis: Any) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app
    from app.services.cache_service import CacheService

    app.state.redis = async_redis
    app.state.cache = CacheService(async_redis, get_settings().cache_ttl_seconds)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
    await dispose_async_engine()


@pytest.fixture
def make_video(db_engine: Any) -> Callable[..., Video]:
    def _make(**overrides: Any) -> Video:
        shortcode = overrides.pop("instagram_id", f"T{uuid.uuid4().hex[:10]}")
        fields: dict[str, Any] = {
            "instagram_id": shortcode,
            "url": f"https://www.instagram.com/reel/{shortcode}/",
            "media_type": "reel",
            "status": VideoStatus.PROCESSING,
        }
        fields.update(overrides)
        with session_scope() as session:
            video = Video(**fields)
            session.add(video)
        return video

    return _make


@pytest.fixture
def nlp() -> Any:
    from app.services.nlp_service import NLPAnalyzer

    analyzer = NLPAnalyzer()
    analyzer.load()
    return analyzer


@pytest.fixture
def pipeline_deps(nlp: Any, sync_redis: Any) -> Callable[..., Any]:
    """Build PipelineDeps with the mock scraper, lexicon NLP and rule-based insights."""
    from app.pipeline import PipelineDeps
    from app.services.cache_service import SyncCacheService
    from app.services.insights_service import InsightsGenerator
    from app.services.scraper_service import InstagramScraper

    def _build(**overrides: Any) -> PipelineDeps:
        fields: dict[str, Any] = {
            "scraper_factory": InstagramScraper,
            "nlp": nlp,
            "insights": InsightsGenerator(),
            "cache": SyncCacheService(client=sync_redis),
        }
        fields.update(overrides)
        return PipelineDeps(**fields)

    return _build
