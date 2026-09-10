"""Background scheduling of collection runs and token refreshes."""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import astimezone
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import session_scope
from app.models import JobLock
from app.services.collector import collect_all_accounts
from app.services.token_service import refresh_expiring_tokens

logger = logging.getLogger(__name__)

COLLECT_JOB_ID = "collect_metrics"
REFRESH_JOB_ID = "refresh_tokens"

# Long enough to cover a slow collection, short enough that a crashed worker
# does not block the job until someone notices.
LOCK_TIMEOUT = timedelta(hours=2)

_scheduler: BackgroundScheduler | None = None


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@contextmanager
def job_lock(job_id: str) -> Iterator[bool]:
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
                db.add(
                    JobLock(job_id=job_id, owner=owner, acquired_at=now,
                            expires_at=now + LOCK_TIMEOUT)
                )
                acquired = True
            elif _as_utc(existing.expires_at) <= now:
                logger.warning(
                    "Taking over the stale %s lock held by %s.", job_id, existing.owner
                )
                existing.owner = owner
                existing.acquired_at = now
                existing.expires_at = now + LOCK_TIMEOUT
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
        try:
            with session_scope() as db:
                held = db.get(JobLock, job_id)
                if held is not None and held.owner == owner:
                    db.delete(held)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not release the %s lock; it will expire.", job_id)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run_collection_job() -> None:
    """Scheduled job: collect metrics for every active account."""
    try:
        with job_lock(COLLECT_JOB_ID) as acquired:
            if not acquired:
                return
            with session_scope() as db:
                results = collect_all_accounts(db, trigger="scheduled")
            logger.info("Scheduled collection finished for %s account(s).", len(results))
    except Exception:  # pragma: no cover - a job must never kill the scheduler
        logger.exception("Scheduled collection failed.")


def run_token_refresh_job() -> None:
    """Scheduled job: refresh tokens that are close to expiring."""
    try:
        with job_lock(REFRESH_JOB_ID) as acquired:
            if not acquired:
                return
            with session_scope() as db:
                results = refresh_expiring_tokens(db)
            if results:
                logger.info("Token refresh processed %s account(s).", len(results))
    except Exception:  # pragma: no cover - a job must never kill the scheduler
        logger.exception("Scheduled token refresh failed.")


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler. Returns None when disabled."""
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false).")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    # Everything in this system is UTC; pinning the triggers too keeps the
    # logged and reported "next run" consistent and immune to DST shifts.
    utc = astimezone("UTC")
    scheduler = BackgroundScheduler(
        timezone=utc,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    scheduler.add_job(
        run_collection_job,
        trigger=IntervalTrigger(minutes=settings.collection_interval_minutes, timezone=utc),
        id=COLLECT_JOB_ID,
        name="Collect Instagram metrics",
        replace_existing=True,
    )
    scheduler.add_job(
        run_token_refresh_job,
        trigger=IntervalTrigger(hours=settings.token_refresh_interval_hours, timezone=utc),
        id=REFRESH_JOB_ID,
        name="Refresh long-lived tokens",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: collection every %s minutes, token refresh every %s hours.",
        settings.collection_interval_minutes,
        settings.token_refresh_interval_hours,
    )
    return scheduler


def shutdown_scheduler() -> None:
    """Stop the scheduler without waiting for running jobs to finish."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)


def next_collection_time() -> datetime | None:
    """When the next automatic collection is due, if the scheduler is on."""
    if not _scheduler or not _scheduler.running:
        return None
    job = _scheduler.get_job(COLLECT_JOB_ID)
    return getattr(job, "next_run_time", None) if job else None
