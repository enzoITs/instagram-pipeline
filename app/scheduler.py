"""Periodic work: what runs, how often, and how workers avoid running it twice.

The schedule itself is executed by Celery beat (`celery -A app.celery_app beat`),
which only decides *when* to publish a task. Two beat processes, or a task that
is retried while an earlier copy is still running, would otherwise collect the
same account twice and spend the Meta quota twice — so every job takes a lock
row in the database, which is the one thing all the processes share.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.database import session_scope
from app.models import JobLock
from app.services.collector import collect_all_accounts
from app.services.token_service import refresh_expiring_tokens

logger = logging.getLogger(__name__)

COLLECT_JOB_ID = "collect_metrics"
REFRESH_JOB_ID = "refresh_tokens"
SWEEP_JOB_ID = "sweep_stale_runs"
TRACKING_JOB_ID = "refresh_tracked_videos"

# Long enough to cover a slow collection, short enough that a crashed worker
# does not block the job until someone notices.
LOCK_TIMEOUT = timedelta(hours=2)


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@contextmanager
def job_lock(job_id: str, timeout: timedelta = LOCK_TIMEOUT) -> Iterator[bool]:
    """Claim *job_id* across every process sharing this database.

    Yields True when the claim succeeded and the job should run, False when
    another worker already holds it. An expired claim is taken over, so a
    process that died mid-job does not stop the schedule permanently.
    """
    owner = _owner_id()
    now = datetime.now(timezone.utc)
    acquired = False

    try:
        with session_scope() as db:
            existing = db.get(JobLock, job_id)
            if existing is None:
                db.add(JobLock(job_id=job_id, owner=owner, acquired_at=now, expires_at=now + timeout))
                acquired = True
            elif _as_utc(existing.expires_at) <= now:
                logger.warning("Taking over the stale %s lock held by %s.", job_id, existing.owner)
                existing.owner = owner
                existing.acquired_at = now
                existing.expires_at = now + timeout
                db.add(existing)
                acquired = True
    except IntegrityError:
        # Another worker inserted the same row in the gap between the read and
        # the write; it won, and this one simply skips the run.
        acquired = False

    if not acquired:
        logger.info("Skipping %s: another worker holds the lock.", job_id)
        yield False
        return

    try:
        yield True
    finally:
        release_job_lock(job_id, owner)


def release_job_lock(job_id: str, owner: str | None = None) -> None:
    """Drop the claim, but only if this process still holds it."""
    owner = owner or _owner_id()
    try:
        with session_scope() as db:
            held = db.get(JobLock, job_id)
            if held is not None and held.owner == owner:
                db.delete(held)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not release the %s lock; it will expire.", job_id)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------- the jobs
def run_collection_job(trigger: str = "scheduled") -> int:
    """Collect metrics for every active account. Returns how many were processed."""
    try:
        with job_lock(COLLECT_JOB_ID) as acquired:
            if not acquired:
                return 0
            with session_scope() as db:
                results = collect_all_accounts(db, trigger=trigger)
            logger.info("Collection finished for %s account(s).", len(results))
            return len(results)
    except Exception:  # pragma: no cover - a job must never kill the worker
        logger.exception("Scheduled collection failed.")
        return 0


def run_token_refresh_job() -> int:
    """Refresh tokens that are close to expiring. Returns how many were processed."""
    try:
        with job_lock(REFRESH_JOB_ID) as acquired:
            if not acquired:
                return 0
            with session_scope() as db:
                results = refresh_expiring_tokens(db)
            if results:
                logger.info("Token refresh processed %s account(s).", len(results))
            return len(results)
    except Exception:  # pragma: no cover
        logger.exception("Scheduled token refresh failed.")
        return 0


def run_stale_sweep_job() -> int:
    """Close collection runs whose worker died. Returns how many were closed."""
    from app.services.jobs_runtime import sweep_stale_runs

    try:
        with job_lock(SWEEP_JOB_ID, timeout=timedelta(minutes=10)) as acquired:
            return sweep_stale_runs() if acquired else 0
    except Exception:  # pragma: no cover
        logger.exception("Stale-run sweep failed.")
        return 0


def run_tracking_job() -> int:
    """Refresh the metrics of tracked link analyses. Returns how many were refreshed."""
    from app.analyzer.tracking import refresh_tracked_videos

    try:
        with job_lock(TRACKING_JOB_ID) as acquired:
            return refresh_tracked_videos() if acquired else 0
    except Exception:  # pragma: no cover
        logger.exception("Tracked-link refresh failed.")
        return 0


# -------------------------------------------------------------- beat schedule
def beat_schedule(settings: Settings | None = None) -> dict[str, Any]:
    """The periodic schedule, as Celery beat expects it.

    `ENABLE_SCHEDULER=false` yields an empty schedule, which is what the test
    suite and any "API only" deployment want.
    """
    settings = settings or get_settings()
    if not settings.enable_scheduler:
        return {}

    schedule: dict[str, Any] = {
        COLLECT_JOB_ID: {
            "task": "app.tasks.collect_all_accounts",
            "schedule": settings.collection_interval_minutes * 60,
            "kwargs": {"trigger": "scheduled"},
        },
        REFRESH_JOB_ID: {
            "task": "app.tasks.refresh_tokens",
            "schedule": settings.token_refresh_interval_hours * 3600,
        },
        SWEEP_JOB_ID: {
            "task": "app.tasks.sweep_stale_runs",
            "schedule": 15 * 60,
        },
    }
    if settings.tracking_interval_minutes:
        schedule[TRACKING_JOB_ID] = {
            "task": "app.tasks.refresh_tracked_videos",
            "schedule": settings.tracking_interval_minutes * 60,
        }
    return schedule


def next_collection_time(db: Any = None) -> datetime | None:
    """Best estimate of the next automatic collection.

    Beat lives in another process, so the schedule is reconstructed from the
    last scheduled run plus the configured interval.
    """
    from sqlalchemy import func, select

    from app.models import CollectionRun

    settings = get_settings()
    if not settings.enable_scheduler:
        return None

    def _last(session: Any) -> datetime | None:
        return session.scalar(
            select(func.max(CollectionRun.started_at)).where(CollectionRun.trigger == "scheduled")
        )

    last = _last(db) if db is not None else None
    if db is None:
        with session_scope() as session:
            last = _last(session)
    if last is None:
        return None
    return _as_utc(last) + timedelta(minutes=settings.collection_interval_minutes)
