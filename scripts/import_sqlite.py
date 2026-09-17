#!/usr/bin/env python
"""Copy the data of an old SQLite install into the configured database.

Earlier releases kept everything in `data/instagram_pipeline.db`; the unified
stack runs on PostgreSQL. This script moves the history across:

    python scripts/import_sqlite.py data/instagram_pipeline.db

The target is whatever `DATABASE_URL` points at (usually the PostgreSQL of
`docker compose up -d postgres`). It must be empty: the script refuses to mix
two histories. The SQLite file itself is never modified — a temporary copy is
brought up to the current schema first, so databases from any older release
(even from before the migrations existed) can be imported.

Access tokens are copied still encrypted. They only stay readable if the new
install uses the same SECRET_KEY (or TOKEN_ENCRYPTION_KEY) as the old one; for
an install that never set it, that is the content of `data/.secret_key`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Engine, create_engine, func, inspect, select, text  # noqa: E402

from app import models  # noqa: E402,F401  (registers the account tables)
from app.analyzer import models as analyzer_models  # noqa: E402,F401  (and the analysis tables)
from app.crypto import TokenDecryptionError, decrypt_token  # noqa: E402
from app.database import BASELINE_REVISION, Base, _alembic_config, get_sync_engine  # noqa: E402

BATCH_SIZE = 500


class ImportRefused(RuntimeError):
    """The import would lose or mix data, so nothing was written."""


@dataclass
class ImportReport:
    rows: dict[str, int] = field(default_factory=dict)
    unreadable_tokens: list[str] = field(default_factory=list)


def migrate_to_head(engine: Engine) -> None:
    """Bring a database to the current schema, stamping pre-Alembic files first."""
    from alembic import command

    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        config = _alembic_config(connection)
        if tables and "alembic_version" not in tables:
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


@contextmanager
def upgraded_copy(sqlite_path: Path) -> Iterator[Engine]:
    """A migrated, throwaway copy of the SQLite file (the original stays untouched)."""
    with tempfile.TemporaryDirectory(prefix="instagram-import-") as tmp:
        copy = Path(tmp) / "source.db"
        shutil.copy2(sqlite_path, copy)
        engine = create_engine(f"sqlite:///{copy}")
        try:
            migrate_to_head(engine)
            yield engine
        finally:
            engine.dispose()


def import_database(source: Engine, target: Engine) -> ImportReport:
    """Copy every application table from *source* into the empty *target*."""
    migrate_to_head(target)
    tables = Base.metadata.sorted_tables  # parents before children

    with target.connect() as connection:
        filled = [t.name for t in tables if connection.scalar(select(func.count()).select_from(t))]
    if filled:
        raise ImportRefused(
            "The target database already holds data (" + ", ".join(filled) + "). "
            "Import into a freshly created database instead."
        )

    report = ImportReport()
    with source.connect() as reader, target.begin() as writer:
        for table in tables:
            copied = 0
            result = reader.execution_options(stream_results=True).execute(select(table))
            while batch := result.mappings().fetchmany(BATCH_SIZE):
                writer.execute(table.insert(), [dict(row) for row in batch])
                copied += len(batch)
            report.rows[table.name] = copied
        if target.dialect.name == "postgresql":
            _reset_sequences(writer, tables)

    with target.connect() as connection:
        for username, token in connection.execute(
            select(models.Account.username, models.Account.access_token_encrypted).where(
                models.Account.is_active.is_(True)
            )
        ):
            try:
                decrypt_token(token)
            except TokenDecryptionError:
                report.unreadable_tokens.append(username or "?")
    return report


def _reset_sequences(connection, tables) -> None:  # noqa: ANN001
    """Explicit ids were inserted, so move each serial past the highest one."""
    for table in tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or not pk[0].autoincrement or pk[0].type.python_type is not int:
            continue
        sequence = connection.scalar(
            text("SELECT pg_get_serial_sequence(:table, :column)"),
            {"table": table.name, "column": pk[0].name},
        )
        if sequence:
            connection.execute(
                text(
                    f'SELECT setval(:sequence, COALESCE((SELECT MAX("{pk[0].name}") FROM "{table.name}"), 1), '
                    f'(SELECT MAX("{pk[0].name}") IS NOT NULL FROM "{table.name}"))'
                ),
                {"sequence": sequence},
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_file", type=Path, help="the old database, e.g. data/instagram_pipeline.db")
    args = parser.parse_args()

    if not args.sqlite_file.is_file():
        print(f"File not found: {args.sqlite_file}", file=sys.stderr)
        return 1

    target = get_sync_engine()
    print(f"Importing {args.sqlite_file} into {target.url.render_as_string(hide_password=True)}")
    try:
        with upgraded_copy(args.sqlite_file) as source:
            report = import_database(source, target)
    except ImportRefused as exc:
        print(f"Nothing was imported: {exc}", file=sys.stderr)
        return 1

    for name, count in report.rows.items():
        if count:
            print(f"  {name}: {count}")
    if report.unreadable_tokens:
        print(
            "\nWARNING: the tokens of these active accounts cannot be decrypted with the current "
            "SECRET_KEY/TOKEN_ENCRYPTION_KEY: " + ", ".join(report.unreadable_tokens) + ".\n"
            "Use the key of the old install (data/.secret_key if none was set) or reconnect them.",
            file=sys.stderr,
        )
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
