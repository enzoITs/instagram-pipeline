"""Celery tasks: link analysis, account collection and the periodic maintenance."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task

from app.celery_app import TASK_NAME, celery_app
from app.config import get_settings


@celery_app.task(bind=True, name=TASK_NAME, max_retries=get_settings().task_max_retries, acks_late=True)
def process_video_analysis(self: Task, video_id: str) -> dict[str, Any]:
    """Analyze one video. Transient failures are retried with exponential backoff
    (3 retries); exhausted or unexpected failures mark the video as failed and are
    pushed to the dead-letter queue `dlq:process_video_analysis`."""
    from app.analyzer.pipeline import execute_attempt

    result = execute_attempt(
        uuid.UUID(video_id),
        attempt=self.request.retries + 1,
        max_retries=self.max_retries or 0,
        task_id=self.request.id,
    )
    if result.status == "retry":
        raise self.retry(countdown=result.countdown, exc=result.error)
    return result.as_dict()


@celery_app.task(name="app.tasks.collect_account", acks_late=True)
def collect_account(account_id: int, run_id: int, limit: int | None = None) -> dict[str, Any]:
    """Collect metrics for one account into the run row the API already created."""
    from app.services.jobs_runtime import run_account_collection

    run_account_collection(account_id, run_id, limit)
    return {"account_id": account_id, "run_id": run_id}


@celery_app.task(name="app.tasks.collect_all_accounts", acks_late=True)
def collect_all_accounts(trigger: str = "scheduled") -> dict[str, Any]:
    """Collect metrics for every active account (the scheduled job, and `POST /api/collect`)."""
    from app.scheduler import run_collection_job

    return {"accounts": run_collection_job(trigger=trigger)}


@celery_app.task(name="app.tasks.refresh_tokens", acks_late=True)
def refresh_tokens() -> dict[str, Any]:
    """Extend the 60-day tokens that are close to expiring."""
    from app.scheduler import run_token_refresh_job

    return {"accounts": run_token_refresh_job()}


@celery_app.task(name="app.tasks.sweep_stale_runs", acks_late=True)
def sweep_stale_runs() -> dict[str, Any]:
    """Close collection runs abandoned by a worker that died mid-collection."""
    from app.services.jobs_runtime import sweep_stale_runs as sweep

    return {"closed": sweep()}


@celery_app.task(name="app.tasks.refresh_tracked_videos", acks_late=True)
def refresh_tracked_videos() -> dict[str, Any]:
    """Take a fresh metrics snapshot of every link the user asked to track."""
    from app.scheduler import run_tracking_job

    return {"videos": run_tracking_job()}
