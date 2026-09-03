"""Background scheduling of collection runs and token refreshes."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import astimezone

from app.config import get_settings
from app.database import session_scope
from app.services.collector import collect_all_accounts
from app.services.token_service import refresh_expiring_tokens

logger = logging.getLogger(__name__)

COLLECT_JOB_ID = "collect_metrics"
REFRESH_JOB_ID = "refresh_tokens"

_scheduler: BackgroundScheduler | None = None


def run_collection_job() -> None:
    """Scheduled job: collect metrics for every active account."""
    try:
        with session_scope() as db:
            results = collect_all_accounts(db, trigger="scheduled")
        logger.info("Scheduled collection finished for %s account(s).", len(results))
    except Exception:  # pragma: no cover - a job must never kill the scheduler
        logger.exception("Scheduled collection failed.")


def run_token_refresh_job() -> None:
    """Scheduled job: refresh tokens that are close to expiring."""
    try:
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


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)


def next_collection_time() -> datetime | None:
    """When the next automatic collection is due, if the scheduler is on."""
    if not _scheduler or not _scheduler.running:
        return None
    job = _scheduler.get_job(COLLECT_JOB_ID)
    return getattr(job, "next_run_time", None) if job else None
