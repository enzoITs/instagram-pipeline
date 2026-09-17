"""Moving an old SQLite install into the unified database."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.crypto import encrypt_token
from app.database import _alembic_config

_SPEC = importlib.util.spec_from_file_location(
    "import_sqlite", Path(__file__).resolve().parent.parent / "scripts" / "import_sqlite.py"
)
assert _SPEC and _SPEC.loader
import_sqlite: Any = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = import_sqlite  # dataclasses look their module up here
_SPEC.loader.exec_module(import_sqlite)


@pytest.fixture()
def old_file(tmp_path: Path) -> Path:
    """A database as the 0003 release left it: one account with some history."""
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "0003")
        connection.execute(
            text(
                "INSERT INTO accounts (id, ig_user_id, username, access_token_encrypted, is_active, "
                "created_at, updated_at) VALUES "
                "(7, 'u7', 'kept.token', :good, 1, '2026-01-01', '2026-01-01'), "
                "(8, 'u8', 'other.key', 'gAAAA-not-ours', 1, '2026-01-01', '2026-01-01')"
            ),
            {"good": encrypt_token("still-readable")},
        )
        connection.execute(
            text(
                "INSERT INTO media (id, account_id, ig_media_id, created_at, updated_at) "
                "VALUES (3, 7, 'm1', '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO metric_snapshots (media_id, collected_at, reach, engagement_rate, "
                "engagement_basis) VALUES (3, '2026-01-02 10:00:00', 500, 4.2, 'reach'), "
                "(3, '2026-01-03 10:00:00', 800, 3.9, 'reach')"
            )
        )
    engine.dispose()
    return path


@pytest.fixture()
def target(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_history_and_ids_are_carried_over(old_file: Path, target: Engine) -> None:
    before = old_file.read_bytes()
    with import_sqlite.upgraded_copy(old_file) as source:
        report = import_sqlite.import_database(source, target)

    assert old_file.read_bytes() == before  # the original is never touched
    assert report.rows["accounts"] == 2
    assert report.rows["metric_snapshots"] == 2
    assert report.unreadable_tokens == ["other.key"]

    with target.connect() as connection:
        assert connection.scalar(text("SELECT account_id FROM media WHERE id = 3")) == 7
        rows = connection.execute(
            text("SELECT reach, engagement_denominator FROM metric_snapshots ORDER BY collected_at")
        ).all()
        # the 0004 back-fill ran on the copy before the rows were moved
        assert [tuple(row) for row in rows] == [(500, 500), (800, 800)]


def test_a_database_with_data_is_refused(old_file: Path, target: Engine) -> None:
    with import_sqlite.upgraded_copy(old_file) as source:
        import_sqlite.import_database(source, target)
        with pytest.raises(import_sqlite.ImportRefused, match="accounts"):
            import_sqlite.import_database(source, target)

    with target.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM accounts")) == 2
