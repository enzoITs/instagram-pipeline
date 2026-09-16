"""Celery tasks."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task

from app.celery_app import TASK_NAME, celery_app
from app.config import get_settings
from app.pipeline import execute_attempt


@celery_app.task(bind=True, name=TASK_NAME, max_retries=get_settings().task_max_retries, acks_late=True)
def process_video_analysis(self: Task, video_id: str) -> dict[str, Any]:
    """Analyze one video. Transient failures are retried with exponential backoff
    (3 retries); exhausted or unexpected failures mark the video as failed and are
    pushed to the dead-letter queue `dlq:process_video_analysis`."""
    result = execute_attempt(
        uuid.UUID(video_id),
        attempt=self.request.retries + 1,
        max_retries=self.max_retries or 0,
        task_id=self.request.id,
    )
    if result.status == "retry":
        raise self.retry(countdown=result.countdown, exc=result.error)
    return result.as_dict()
