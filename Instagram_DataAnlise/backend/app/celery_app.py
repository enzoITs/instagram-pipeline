"""Celery application (Redis broker + result backend).

Start a worker with:
    celery -A app.celery_app worker --loglevel=info
"""

from __future__ import annotations

import logging
import os
from typing import Any

from celery import Celery, signals

from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

TASK_NAME = "app.tasks.process_video_analysis"
QUEUE_NAME = "analysis"

settings = get_settings()

celery_app = Celery(
    "instagram_analyzer",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

_concurrency = os.environ.get("CELERY_CONCURRENCY")

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue=QUEUE_NAME,
    # Reliability: ack only after completion and re-queue if a worker dies mid-task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_time_limit=settings.task_time_limit_seconds,
    # Must exceed the hard time limit, otherwise Redis re-delivers long-running tasks.
    broker_transport_options={"visibility_timeout": 3600},
    task_track_started=True,
    result_expires=86_400,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    # Fail fast when the broker is down so the API can answer 503 instead of hanging.
    task_publish_retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
    # Events for monitoring tools (celery events / Flower).
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Default = one process per CPU core. Each process loads the NLP models, so
    # memory-constrained hosts should cap it via CELERY_CONCURRENCY.
    worker_concurrency=int(_concurrency) if _concurrency else None,
)


@signals.setup_logging.connect
def _setup_logging(**_kwargs: Any) -> None:
    """Use our structured logging instead of Celery's default handlers."""
    configure_logging()


def torch_threads_per_process(cores: int | None = None, processes: int | None = None) -> int:
    """Split CPU cores between worker processes: N processes x all-cores threads each
    oversubscribes the CPU and makes every job slower."""
    cores = cores or os.cpu_count() or 1
    processes = processes or cores
    return max(1, cores // processes)


@signals.worker_process_init.connect
def _init_worker_process(**_kwargs: Any) -> None:
    """Runs in every forked child: fresh connection pools, then preload and warm up NLP models."""
    from app.database import reset_sync_engine
    from app.services.cache_service import get_sync_redis

    reset_sync_engine()
    get_sync_redis.cache_clear()

    if settings.nlp_torch_threads is None:
        settings.nlp_torch_threads = torch_threads_per_process(processes=celery_app.conf.worker_concurrency)

    from app.services.nlp_service import get_nlp_analyzer

    analyzer = get_nlp_analyzer()
    analyzer.load()
    analyzer.warm_up()
    logger.info(
        "worker_process_ready",
        extra={"pid": os.getpid(), "nlp_backend": analyzer.backend, "torch_threads": settings.nlp_torch_threads},
    )
