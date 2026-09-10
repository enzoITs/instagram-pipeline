"""Database engine, session factory and declarative base."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, TypeDecorator, create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BASE_DIR, get_settings

if TYPE_CHECKING:
    from alembic.config import Config

logger = logging.getLogger(__name__)

settings = get_settings()

MIGRATIONS_DIR = BASE_DIR / "migrations"

# The revision describing the schema as it stood before Alembic was introduced.
# Databases created by the old `create_all` path are stamped here so the later
# revisions can be applied to them instead of failing.
BASELINE_REVISION = "0001"

_connect_args: dict[str, object] = {}
if settings.database_url.startswith("sqlite"):
    # FastAPI runs sync endpoints in a threadpool, so a connection may be used
    # from a different thread than the one that created it.
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    echo=False,
    future=True,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enable foreign keys and WAL mode on SQLite connections."""
    if engine.dialect.name != "sqlite":
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


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager used by background jobs and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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

    A brand-new database is created straight from the models and stamped at the
    latest revision. An existing one is migrated: databases predating Alembic
    carry no version table, so they are stamped at the baseline first and then
    upgraded. Doing this at startup means a routine `git pull` never leaves the
    schema behind the code.
    """
    from alembic import command

    from app import models  # noqa: F401  (registers the models on Base.metadata)

    tables = set(inspect(engine).get_table_names())

    with _migration_connection() as connection:
        config = _alembic_config(connection)
        if not tables:
            Base.metadata.create_all(bind=connection)
            command.stamp(config, "head")
            logger.info("Created a new database at the latest schema revision.")
            return
        if "alembic_version" not in tables:
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
