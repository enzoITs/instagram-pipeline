"""Running collections outside the request/response cycle.

A full collection is dozens of sequential Graph API calls, and Meta's own
backoff header can add minutes on top. Holding an HTTP request open for that
long means proxies and browsers time out on work that actually succeeded, so
the API hands back a run id immediately and the work continues here.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import session_scope
from app.models import Account, CollectionRun, RunStatus, utcnow
from app.services.collector import collect_account, collect_all_accounts

logger = logging.getLogger(__name__)


def has_running_collection(db, account_id: int) -> bool:  # noqa: ANN001
    """True when a collection for this account is already in flight."""
    return (
        db.scalar(
            select(CollectionRun.id).where(
                CollectionRun.account_id == account_id,
                CollectionRun.status == RunStatus.RUNNING,
            )
        )
        is not None
    )


def run_account_collection(account_id: int, run_id: int, limit: int | None) -> None:
    """Background worker for a single account's collection."""
    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            run = db.get(CollectionRun, run_id)
            if account is None or run is None:  # pragma: no cover - deleted mid-flight
                logger.warning("Collection %s no longer has an account to run for.", run_id)
                return
            collect_account(db, account, run=run, trigger=run.trigger, limit=limit)
    except Exception:
        logger.exception("Background collection %s crashed.", run_id)
        _mark_failed(run_id, "The collection crashed. See the server log for details.")


def run_all_accounts_collection(trigger: str = "manual") -> None:
    """Background worker for the collect-everything endpoint."""
    try:
        with session_scope() as db:
            collect_all_accounts(db, trigger=trigger)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Background collection of all accounts crashed.")


def _mark_failed(run_id: int, message: str) -> None:
    """Best-effort: never leave a run stuck on `running` after a crash."""
    try:
        with session_scope() as db:
            run = db.get(CollectionRun, run_id)
            if run is not None and run.status == RunStatus.RUNNING:
                run.status = RunStatus.FAILED
                run.finished_at = utcnow()
                run.error = message
                db.add(run)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not mark run %s as failed.", run_id)


def sweep_orphaned_runs() -> int:
    """Close runs left `running` by a process that died. Returns how many.

    Without this a crash during a collection leaves a row that blocks every
    later collection for that account, since one run at a time is allowed.
    """
    with session_scope() as db:
        orphans = list(
            db.scalars(select(CollectionRun).where(CollectionRun.status == RunStatus.RUNNING))
        )
        for run in orphans:
            run.status = RunStatus.FAILED
            run.finished_at = utcnow()
            run.error = (
                "Interrupted: the server stopped while this collection was running."
            )
            db.add(run)
    if orphans:
        logger.warning("Closed %s collection run(s) interrupted by a restart.", len(orphans))
    return len(orphans)
