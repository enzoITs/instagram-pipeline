"""Running collections outside the request/response cycle.

A full collection is dozens of sequential Graph API calls, and Meta's own
backoff header can add minutes on top. Holding an HTTP request open for that
long means proxies and browsers time out on work that actually succeeded, so
the API hands back a run id immediately and a Celery worker does the work.
`TASK_BACKEND=inline` runs the same functions in the API process, which is how
the test suite exercises them without a broker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import session_scope
from app.models import Account, CollectionRun, RunStatus, utcnow
from app.services.collector import collect_account, collect_all_accounts

logger = logging.getLogger(__name__)


class QueueUnavailableError(RuntimeError):
    """The Celery broker refused the job; the caller turns this into a 503."""


def has_running_collection(db: Session, account_id: int) -> bool:
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


# --------------------------------------------------------------------- dispatch
def dispatch_account_collection(
    account_id: int,
    run_id: int,
    limit: int | None,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Hand one account's collection to the worker (or to the request's background)."""
    settings = get_settings()
    if settings.task_backend == "inline":
        if background_tasks is None:  # pragma: no cover - defensive
            run_account_collection(account_id, run_id, limit)
        else:
            background_tasks.add_task(run_account_collection, account_id, run_id, limit)
        return

    from app.tasks import collect_account as collect_account_task

    try:
        collect_account_task.delay(account_id, run_id, limit)
    except Exception as exc:  # noqa: BLE001 - broker unreachable
        logger.error("collection_enqueue_failed", extra={"run_id": run_id, "error": type(exc).__name__})
        _mark_failed(run_id, "Fila de processamento indisponível. Tente novamente em instantes.")
        raise QueueUnavailableError(str(exc)) from exc


def dispatch_all_accounts_collection(
    trigger: str = "manual", background_tasks: BackgroundTasks | None = None
) -> None:
    """Hand the collect-everything job to the worker (or to the request's background)."""
    settings = get_settings()
    if settings.task_backend == "inline":
        if background_tasks is None:  # pragma: no cover - defensive
            run_all_accounts_collection(trigger)
        else:
            background_tasks.add_task(run_all_accounts_collection, trigger)
        return

    from app.tasks import collect_all_accounts as collect_all_task

    try:
        collect_all_task.delay(trigger)
    except Exception as exc:  # noqa: BLE001
        logger.error("collection_enqueue_failed", extra={"trigger": trigger, "error": type(exc).__name__})
        raise QueueUnavailableError(str(exc)) from exc


# ----------------------------------------------------------------- job bodies
def run_account_collection(account_id: int, run_id: int, limit: int | None) -> None:
    """Worker body for a single account's collection."""
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
    """Worker body for the collect-everything endpoint."""
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


def sweep_stale_runs(older_than_minutes: int | None = None) -> int:
    """Close runs abandoned by a process that died. Returns how many.

    Only runs untouched for longer than `COLLECTION_STALE_AFTER_MINUTES` are
    closed: with workers in their own processes, "running" rows usually belong
    to a collection that is very much alive, and closing those would let a
    second collection start on top of the first.
    """
    settings = get_settings()
    threshold = datetime.now(timezone.utc) - timedelta(
        minutes=older_than_minutes or settings.collection_stale_after_minutes
    )
    with session_scope() as db:
        orphans = list(
            db.scalars(
                select(CollectionRun).where(
                    CollectionRun.status == RunStatus.RUNNING,
                    CollectionRun.started_at < threshold,
                )
            )
        )
        for run in orphans:
            run.status = RunStatus.FAILED
            run.finished_at = utcnow()
            run.error = "Interrupted: the worker running this collection stopped responding."
            db.add(run)
    if orphans:
        logger.warning("Closed %s collection run(s) left behind by a stopped worker.", len(orphans))
    return len(orphans)
