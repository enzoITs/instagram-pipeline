"""Shared pytest fixtures for both halves of the app.

Environment variables are set *before* any `app.*` import so the application
picks up the throwaway database, the fake queue and the mock collectors instead
of the real ones. The schema is created by the Alembic migrations, so the test
suite exercises the migration chain the deployments run.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="instagram-pipeline-tests-"))

os.environ.update(
    {
        # --- infrastructure: SQLite + fakeredis, no services required --------
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP_DIR / 'test.db'}"),
        "REDIS_URL": "redis://127.0.0.1:6399/0",
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "TASK_BACKEND": "inline",
        "ENABLE_SCHEDULER": "false",
        "AUTO_MIGRATE": "true",
        # --- app ------------------------------------------------------------
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret-key-for-the-test-suite-only",
        "TOKEN_ENCRYPTION_KEY": "",
        "ADMIN_PASSWORD": "",
        "PUBLIC_BASE_URL": "https://example.test",
        "FRONTEND_DIST_DIR": str(_TMP_DIR / "frontend-dist"),
        "LOG_FORMAT": "text",
        "LOG_LEVEL": "WARNING",
        "RATE_LIMIT_PER_MINUTE": "10",
        "LOGIN_ATTEMPTS_PER_MINUTE": "5",
        "TRUST_PROXY_HEADERS": "false",
        "CORS_ORIGINS": "http://localhost:5173,http://localhost:3000",
        # --- Meta -----------------------------------------------------------
        "INSTAGRAM_APP_ID": "test-app-id",
        "INSTAGRAM_APP_SECRET": "test-app-secret",
        "META_LOGIN_FLOW": "instagram_login",
        "GRAPH_API_VERSION": "v23.0",
        "MAX_RETRIES": "2",
        # --- link analysis: synthetic data, no models, no paid APIs ----------
        "SCRAPER_MODE": "mock",
        "NLP_BACKEND": "lexicon",
        "OPENAI_API_KEY": "",
        "APIFY_TOKEN": "",
        "INSTAGRAM_USERNAME": "",
        "INSTAGRAM_SESSIONFILE": "",
        "CACHE_TTL_SECONDS": "86400",
    }
)

import fakeredis  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.crypto import encrypt_token  # noqa: E402
from app.database import Base, SessionLocal, init_db  # noqa: E402
from app.instagram.client import InstagramClient, RateLimiter  # noqa: E402
from app.models import Account  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Build the schema through the migrations, once for the whole session."""
    init_db()


@pytest.fixture(scope="session", autouse=True)
def _fake_dashboard_bundle() -> None:
    """Stand-in for `npm run build`, so serving the SPA is covered by the suite."""
    dist = Path(os.environ["FRONTEND_DIST_DIR"])
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Instagram Pipeline</title>"
        '<script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("export default null;\n", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")


def truncate_all() -> None:
    """Empty every table, children first, without dropping the schema."""
    session = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def db() -> Iterator[Session]:
    """A clean database session; every table is emptied before each test."""
    truncate_all()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def settings() -> Any:
    return get_settings()


@pytest.fixture()
def account(db: Session) -> Account:
    """A connected account with a usable encrypted token."""
    row = Account(
        ig_user_id="17841400000000001",
        username="test.account",
        account_type="BUSINESS",
        followers_count=10_000,
        access_token_encrypted=encrypt_token("long-lived-token"),
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def api_client(settings: Any) -> Iterator[InstagramClient]:
    """A Graph client that never actually sleeps, so retries stay fast."""
    client = InstagramClient(
        settings,
        rate_limiter=RateLimiter(10_000),
        sleeper=lambda _seconds: None,
    )
    try:
        yield client
    finally:
        client.close()


# --------------------------------------------------------------------- Redis
@pytest.fixture
def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def sync_redis(fake_server: fakeredis.FakeServer, monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
    import app.analyzer.pipeline
    import app.analyzer.services.cache_service

    monkeypatch.setattr(app.analyzer.services.cache_service, "get_sync_redis", lambda: client)
    monkeypatch.setattr(app.analyzer.pipeline, "get_sync_redis", lambda: client)
    return client


@pytest.fixture
async def async_redis(fake_server: fakeredis.FakeServer) -> Any:
    client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------- HTTP client
@pytest.fixture()
def http_client(db: Session, fake_server: fakeredis.FakeServer) -> Iterator[TestClient]:
    """FastAPI test client. The `db` fixture guarantees a clean database."""
    from app.analyzer.services.cache_service import CacheService
    from app.main import app

    # https, because PUBLIC_BASE_URL is https in this suite and the OAuth state
    # cookie is therefore marked Secure — a plain-http client would drop it.
    with TestClient(app, base_url="https://testserver") as client:
        redis_client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
        app.state.redis = redis_client
        app.state.cache = CacheService(redis_client, get_settings().cache_ttl_seconds)
        yield client
