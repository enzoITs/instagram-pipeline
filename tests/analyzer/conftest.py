"""Fixtures for the link-analysis half of the suite.

The environment, the schema and the fake Redis come from `tests/conftest.py`;
what is added here is the async HTTP client and the pipeline's dependencies
wired to the mock scraper, the lexicon NLP backend and rule-based insights.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.database import dispose_async_engine, get_sync_engine, session_scope
from tests.conftest import truncate_all


@pytest.fixture
def db_engine() -> Iterator[Any]:
    """A clean database for a test that talks to the models directly."""
    truncate_all()
    yield get_sync_engine()
    truncate_all()


@pytest.fixture
async def client(db_engine: Any, async_redis: Any, sync_redis: Any) -> AsyncIterator[httpx.AsyncClient]:
    from app.analyzer.services.cache_service import CacheService
    from app.main import app

    app.state.redis = async_redis
    app.state.cache = CacheService(async_redis, get_settings().cache_ttl_seconds)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
    await dispose_async_engine()


@pytest.fixture
def make_video(db_engine: Any) -> Callable[..., Any]:
    from app.analyzer.models import Video, VideoStatus

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
    from app.analyzer.services.nlp_service import NLPAnalyzer

    analyzer = NLPAnalyzer()
    analyzer.load()
    return analyzer


@pytest.fixture
def pipeline_deps(nlp: Any, sync_redis: Any) -> Callable[..., Any]:
    """PipelineDeps with the mock scraper, lexicon NLP and rule-based insights.

    `graph_loader` returns None by default: a test that wants the connected-account
    path passes its own loader.
    """
    from app.analyzer.pipeline import PipelineDeps
    from app.analyzer.services.cache_service import SyncCacheService
    from app.analyzer.services.insights_service import InsightsGenerator
    from app.analyzer.services.scraper_service import InstagramScraper

    def _build(**overrides: Any) -> PipelineDeps:
        fields: dict[str, Any] = {
            "scraper_factory": InstagramScraper,
            "nlp": nlp,
            "insights": InsightsGenerator(),
            "cache": SyncCacheService(client=sync_redis),
            "graph_loader": lambda _session, _parsed: None,
        }
        fields.update(overrides)
        return PipelineDeps(**fields)

    return _build
