"""Database engines, session factories, the declarative base and migrations.

Two engines point at the same database:

* a blocking engine for the account routes, the Celery workers and Alembic;
* an asyncio engine for the link-analysis routes, which were written async.

Both come from one ``DATABASE_URL`` and, on PostgreSQL, one driver (psycopg 3
speaks both). The two worlds never share a session: mixing sync and async
SQLAlchemy contexts is a classic source of ``greenlet_spawn`` errors.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, TypeDecorator, create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BASE_DIR, Settings, get_settings

if TYPE_CHECKING:
    from alembic.config import Config

logger = logging.getLogger(__name__)

settings = get_settings()

MIGRATIONS_DIR = BASE_DIR / "migrations"

# The revision describing the schema as it stood before Alembic was introduced.
# Databases created by the old `create_all` path are stamped here so the later
# revisions can be applied to them instead of failing.
BASELINE_REVISION = "0001"


def _engine_options(config: Settings) -> dict[str, Any]:
    if config.is_sqlite:
        # FastAPI runs sync endpoints in a threadpool, so a connection may be
        # used from a different thread than the one that created it.
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {
        "pool_size": config.db_pool_size,
        "max_overflow": config.db_max_overflow,
        "pool_timeout": config.db_pool_timeout_seconds,
        "pool_recycle": 1800,
        "connect_args": {"options": f"-c statement_timeout={config.db_statement_timeout_ms}"},
    }


engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,
    future=True,
    **_engine_options(settings),
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enable foreign keys and WAL mode on SQLite connections (sync and async)."""
    if not settings.is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class UTCDateTime(TypeDecorator):
    """A timestamp column that is always timezone-aware UTC in Python.

    SQLite has no native timestamp type and drops the offset, so values read
    back would otherwise be naive — which the browser would then interpret as
    local time and display several hours off. This decorator normalises to UTC
    on the way in and re-attaches UTC on the way out, for every backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


# ------------------------------------------------------------------ sync side
def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for jobs and scripts: commit on success, rollback on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get_sync_engine() -> Engine:
    return engine


def reset_sync_engine() -> None:
    """Drop pooled connections inherited across a fork (Celery prefork children).

    ``close=False`` leaves the parent's sockets alone and simply stops this
    process from reusing them.
    """
    engine.dispose(close=False)


# ----------------------------------------------------------------- async side
@lru_cache
def get_async_engine() -> AsyncEngine:
    """Created lazily, so each process builds its own pool after start-up/fork."""
    config = get_settings()
    return create_async_engine(config.async_database_url, pool_pre_ping=True, **_engine_options(config))


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one async session per request, rolled back if not committed."""
    async with get_async_sessionmaker()() as session:
        yield session


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


# ----------------------------------------------------------------- migrations
def _alembic_config(connection) -> Config:  # noqa: ANN001
    """Build an Alembic config bound to an already-open connection.

    Passing the connection avoids re-reading the URL — and avoids Alembic's
    ini interpolation choking on a password containing a percent sign.
    """
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def init_db() -> None:
    """Bring the database up to the current schema.

    Every database — including a brand-new one — goes through the migration
    chain, so the schema the app runs on is exactly the one the migrations
    describe (and the test suite exercises them). Databases predating Alembic
    carry tables but no version table; they are stamped at the baseline first
    and then upgraded. Doing this at startup means a routine `git pull` never
    leaves the schema behind the code.
    """
    from alembic import command

    from app import models  # noqa: F401  (registers the models on Base.metadata)

    tables = set(inspect(engine).get_table_names())

    with _migration_connection() as connection:
        config = _alembic_config(connection)
        if tables and "alembic_version" not in tables:
            command.stamp(config, BASELINE_REVISION)
            logger.info(
                "Existing database had no migration history; stamped it at %s.",
                BASELINE_REVISION,
            )
        command.upgrade(config, "head")


@contextmanager
def _migration_connection() -> Iterator[Connection]:
    """Yield a connection safe to run schema changes on.

    SQLite cannot alter a constraint in place, so Alembic rebuilds the table:
    it copies the rows aside, DROPs the original and renames the copy. With
    `PRAGMA foreign_keys=ON` — which this app sets on every connection — that
    DROP cascades into the child tables and silently deletes the entire metric
    history. Enforcement is therefore suspended for the duration of the
    migration, which is the documented SQLite procedure, and the whole thing
    runs outside a transaction because the pragma is ignored inside one.

    Every other backend keeps its normal transactional DDL.
    """
    if engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            yield connection
        return

    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        yield connection
        # A rebuild can leave dangling rows if the data was already
        # inconsistent; surface that rather than re-enabling checks over it.
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            logger.error(
                "Foreign key violations after migrating: %s. The schema changed "
                "but some rows now point at missing parents.",
                violations[:5],
            )
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.close()
