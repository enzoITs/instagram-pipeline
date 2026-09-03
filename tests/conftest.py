"""Shared pytest fixtures.

Environment variables are set *before* any `app.*` import so the application
picks up the throwaway test database instead of the real one.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="instagram-pipeline-tests-"))

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP_DIR / 'test.db'}",
        "SECRET_KEY": "test-secret-key-for-the-test-suite-only",
        "TOKEN_ENCRYPTION_KEY": "",
        "ENABLE_SCHEDULER": "false",
        "ENVIRONMENT": "test",
        "INSTAGRAM_APP_ID": "test-app-id",
        "INSTAGRAM_APP_SECRET": "test-app-secret",
        "PUBLIC_BASE_URL": "https://example.test",
        "META_LOGIN_FLOW": "instagram_login",
        "GRAPH_API_VERSION": "v23.0",
        "MAX_RETRIES": "2",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.crypto import encrypt_token  # noqa: E402
from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.instagram.client import InstagramClient, RateLimiter  # noqa: E402
from app.models import Account  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    init_db()


@pytest.fixture()
def db() -> Session:
    """A clean database session; every table is emptied before each test."""
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def settings():
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
def api_client(settings) -> InstagramClient:
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


@pytest.fixture()
def http_client(db: Session) -> TestClient:
    """FastAPI test client. The `db` fixture guarantees a clean database."""
    from app.main import app

    with TestClient(app) as client:
        yield client
