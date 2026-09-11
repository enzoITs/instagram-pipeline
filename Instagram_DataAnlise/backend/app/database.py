"""Database engines and session factories.

The API runs on the async engine (asyncpg). The Celery worker and Alembic run on a
blocking engine (psycopg2). The two worlds never share engines or sessions: mixing
sync and async SQLAlchemy contexts is a classic source of "greenlet_spawn" errors.

Engines are created lazily so that each process (API worker, forked Celery child)
builds its own connection pool after start-up/fork.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

# The worker only ever holds one session at a time per process, so it needs a small pool.
_SYNC_POOL_SIZE = 2
_SYNC_MAX_OVERFLOW = 3


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per connection."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@lru_cache
def get_async_engine() -> AsyncEngine:
    settings = get_settings()
    if settings.is_sqlite:
        engine = create_async_engine(settings.database_url, connect_args={"timeout": 30})
        _enable_sqlite_foreign_keys(engine.sync_engine)
        return engine
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_min,
        max_overflow=max(settings.db_pool_max - settings.db_pool_min, 0),
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)}
        },
    )


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back if not committed."""
    async with get_async_sessionmaker()() as session:
        yield session


@lru_cache
def get_sync_engine() -> Engine:
    settings = get_settings()
    if settings.is_sqlite:
        engine = create_engine(
            settings.sync_database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        _enable_sqlite_foreign_keys(engine)
        return engine
    return create_engine(
        settings.sync_database_url,
        pool_size=_SYNC_POOL_SIZE,
        max_overflow=_SYNC_MAX_OVERFLOW,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"options": f"-c statement_timeout={settings.db_statement_timeout_ms}"},
    )


@lru_cache
def get_sync_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for the worker: commit on success, rollback on error."""
    session = get_sync_sessionmaker()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


async def check_database(timeout: float = 2.0) -> bool:
    """Health probe: `SELECT 1` bounded by `timeout` seconds."""

    async def _probe() -> None:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_probe(), timeout=timeout)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means "disconnected"
        logger.warning("database_health_check_failed", extra={"error": type(exc).__name__})
        return False


async def dispose_async_engine() -> None:
    if get_async_engine.cache_info().currsize:
        await get_async_engine().dispose()
    get_async_sessionmaker.cache_clear()
    get_async_engine.cache_clear()


def reset_sync_engine() -> None:
    """Drop the blocking engine (used after a process fork and in tests)."""
    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose(close=False)
    get_sync_sessionmaker.cache_clear()
    get_sync_engine.cache_clear()
