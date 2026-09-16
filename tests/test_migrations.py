"""The migration chain, on a fresh database and on one that already holds data.

The rest of the suite already runs on a schema built by the migrations; these
tests cover what it cannot: upgrading a database created by an older release
without losing its history, and walking the chain back down.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.database import _alembic_config


@pytest.fixture()
def scratch_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
    try:
        yield engine
    finally:
        engine.dispose()


def _migrate(engine: Engine, action: str, revision: str) -> None:
    with engine.begin() as connection:
        getattr(command, action)(_alembic_config(connection), revision)


def test_an_empty_database_reaches_head(scratch_engine: Engine) -> None:
    _migrate(scratch_engine, "upgrade", "head")

    tables = set(inspect(scratch_engine).get_table_names())
    assert {"accounts", "media", "metric_snapshots", "collection_runs", "job_locks"} <= tables
    assert {"videos", "comments", "engagement_metrics", "video_metric_snapshots"} <= tables


def test_an_existing_database_keeps_its_history(scratch_engine: Engine) -> None:
    _migrate(scratch_engine, "upgrade", "0003")
    with scratch_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO accounts (id, ig_user_id, username, access_token_encrypted, is_active, "
                "created_at, updated_at) VALUES (1, 'u1', 'old.account', 'x', 1, "
                "'2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media (id, account_id, ig_media_id, created_at, updated_at) "
                "VALUES (1, 1, 'm1', '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO metric_snapshots (media_id, collected_at, reach, engagement_rate, "
                "engagement_basis) VALUES (1, '2026-01-02', 500, 4.2, 'reach')"
            )
        )
        # Two runs left "running" for one account: only the newest may survive
        # the one-run-per-account index.
        for run_id in (1, 2):
            connection.execute(
                text(
                    "INSERT INTO collection_runs (id, account_id, started_at, status, trigger, "
                    "media_seen, media_created, snapshots_created, api_calls) "
                    f"VALUES ({run_id}, 1, '2026-01-0{run_id}', 'RUNNING', 'manual', 0, 0, 0, 0)"
                )
            )

    _migrate(scratch_engine, "upgrade", "head")

    with scratch_engine.connect() as connection:
        assert connection.scalar(text("SELECT username FROM accounts")) == "old.account"
        snapshot = connection.execute(
            text("SELECT engagement_rate, engagement_denominator FROM metric_snapshots")
        ).one()
        assert tuple(snapshot) == (4.2, 500)
        statuses = dict(connection.execute(text("SELECT id, status FROM collection_runs")).all())
        assert statuses == {1: "FAILED", 2: "RUNNING"}


def test_the_link_analysis_tables_can_be_rolled_back(scratch_engine: Engine) -> None:
    _migrate(scratch_engine, "upgrade", "head")
    _migrate(scratch_engine, "downgrade", "0003")

    tables = set(inspect(scratch_engine).get_table_names())
    assert "videos" not in tables
    assert "accounts" in tables

    _migrate(scratch_engine, "upgrade", "head")
    assert "videos" in inspect(scratch_engine).get_table_names()
