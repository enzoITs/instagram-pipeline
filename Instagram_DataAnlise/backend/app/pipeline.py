"""The 8-step analysis pipeline, executed by the Celery worker (or inline in development).

    1 prepare -> 2 scrape -> 3 store -> 4 NLP -> 5 store NLP -> 6 metrics -> 7 insights -> 8 cache

Each step commits its own short transaction and bumps `videos.current_step`, so the API
can report progress and a crash never leaves a half-written analysis marked completed.
The whole run is idempotent: a retry wipes the previous partial results first.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import session_scope
from app.models import (
    Comment,
    EngagementMetrics,
    Insight,
    IntentCategory,
    SentimentAnalysis,
    SentimentLabel,
    TopicAnalysis,
    Video,
    VideoStatus,
    utcnow,
)
from app.repository import load_analysis_sync, upsert_cache_backup
from app.schemas import STEP_LABELS, TOTAL_STEPS
from app.services.cache_service import SyncCacheService, get_sync_redis
from app.services.engagement import calculate_engagement_rate, calculate_engagement_score
from app.services.insights_service import InsightContext, InsightsGenerator
from app.services.scraper_service import InstagramScraper, ScrapedPost, ScraperError

logger = logging.getLogger(__name__)

DLQ_KEY = "dlq:process_video_analysis"
DLQ_MAX_LENGTH = 1000


class AnalysisCancelled(Exception):
    """The video row disappeared (deleted by the user) while the job was running."""


class TransientAnalysisError(Exception):
    """Temporary failure: the job should be retried with backoff."""


class PermanentAnalysisError(Exception):
    """Failure that retrying cannot fix (e.g. private or deleted post)."""


@dataclass
class PipelineDeps:
    scraper_factory: Callable[[], AbstractContextManager[InstagramScraper]]
    nlp: Any
    insights: InsightsGenerator
    cache: SyncCacheService


def default_deps() -> PipelineDeps:
    from app.services.nlp_service import get_nlp_analyzer  # heavy import only inside the worker

    return PipelineDeps(
        scraper_factory=InstagramScraper,
        nlp=get_nlp_analyzer(),
        insights=InsightsGenerator(),
        cache=SyncCacheService(),
    )


class StepTimer:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.durations: dict[str, float] = {}

    def measure(self, name: str) -> _Measure:
        return _Measure(self, name)

    @property
    def total(self) -> float:
        return round(time.perf_counter() - self.started, 3)


class _Measure:
    def __init__(self, timer: StepTimer, name: str) -> None:
        self.timer, self.name = timer, name

    def __enter__(self) -> None:
        self.start = time.perf_counter()

    def __exit__(self, *_exc: object) -> None:
        self.timer.durations[self.name] = round(time.perf_counter() - self.start, 3)


# --------------------------------------------------------------------------- pipeline
def run_analysis(video_id: uuid.UUID, *, attempt: int = 1, deps: PipelineDeps | None = None) -> dict[str, Any]:
    deps = deps or default_deps()
    settings = get_settings()
    timer = StepTimer()
    log_ctx = {"video_id": str(video_id), "attempt": attempt}
    logger.info("analysis_started", extra=log_ctx)

    # 1. Prepare --------------------------------------------------------------
    with session_scope() as session:
        video = _get_video(session, video_id)
        video.status = VideoStatus.PROCESSING
        video.error_message = None
        video.current_step = 1
        url, instagram_id = video.url, video.instagram_id

    # 2. Scrape ---------------------------------------------------------------
    _advance(video_id, 2)
    with timer.measure("scraping"):
        try:
            with deps.scraper_factory() as scraper:
                post = scraper.scrape(url)
        except ScraperError as exc:
            if exc.transient:
                raise TransientAnalysisError(str(exc)) from exc
            raise PermanentAnalysisError(
                f"Não foi possível acessar o post ({exc}). Verifique se o link está correto e se o post é público."
            ) from exc

    # 3. Store metadata, metrics and comments --------------------------------------
    comment_ids = [uuid.uuid4() for _ in post.comments]
    with timer.measure("storage"), session_scope() as session:
        video = _get_video(session, video_id)
        video.current_step = 3
        _apply_post_metadata(video, post)
        _clear_previous_results(session, video_id)
        session.add(
            EngagementMetrics(
                video_id=video_id,
                views=post.views,
                likes=post.likes,
                comments_count=post.comments_count,
                shares=post.shares,
                saves=post.saves,
            )
        )
        if post.comments:
            session.execute(
                insert(Comment),
                [
                    {
                        "id": comment_pk,
                        "video_id": video_id,
                        "comment_id": comment.comment_id,
                        "author": comment.author,
                        "text": comment.text,
                        "likes": comment.likes,
                        "created_at": comment.created_at,
                    }
                    for comment_pk, comment in zip(comment_ids, post.comments)
                ],
            )

    # 4. NLP ------------------------------------------------------------------
    _advance(video_id, 4)
    with timer.measure("nlp"):
        nlp_result = deps.nlp.analyze([comment.text for comment in post.comments])
    analyses = nlp_result.comments

    # 5. Store NLP results --------------------------------------------------------
    with timer.measure("nlp_storage"), session_scope() as session:
        _get_video(session, video_id).current_step = 5
        if analyses:
            session.execute(
                insert(SentimentAnalysis),
                [
                    {
                        "comment_id": comment_pk,
                        "sentiment": analysis.sentiment,
                        "confidence": analysis.confidence,
                        "intent_category": analysis.intent_category,
                        "key_phrases": analysis.key_phrases,
                    }
                    for comment_pk, analysis in zip(comment_ids, analyses)
                ],
            )
        if nlp_result.topics:
            session.execute(
                insert(TopicAnalysis),
                [
                    {
                        "video_id": video_id,
                        "topic": topic.topic,
                        "frequency": topic.frequency,
                        "sentiment_avg": topic.sentiment_avg,
                        "key_keywords": topic.key_keywords,
                    }
                    for topic in nlp_result.topics
                ],
            )

    # 6. Engagement calculations ----------------------------------------------------
    with timer.measure("calculations"):
        sentiment_counts = Counter(analysis.sentiment.value for analysis in analyses)
        intent_counts = Counter(analysis.intent_category.value for analysis in analyses)
        non_spam = len(analyses) - sentiment_counts.get(SentimentLabel.SPAM.value, 0)
        positive_ratio = sentiment_counts.get(SentimentLabel.POSITIVE.value, 0) / non_spam if non_spam else 0.0
        engagement_rate = calculate_engagement_rate(
            post.views, post.likes, post.comments_count, post.shares, post.saves
        )
        engagement_score = calculate_engagement_score(engagement_rate, positive_ratio)
        with session_scope() as session:
            _get_video(session, video_id).current_step = 6
            session.execute(
                update(EngagementMetrics)
                .where(EngagementMetrics.video_id == video_id)
                .values(engagement_rate=engagement_rate, engagement_score=engagement_score)
            )

    # 7. AI insights ----------------------------------------------------------
    _advance(video_id, 7)
    with timer.measure("insights"):
        context = InsightContext(
            caption=post.caption,
            creator_username=post.creator_username,
            media_type=post.media_type,
            duration_sec=post.duration_sec,
            metrics={
                "views": post.views,
                "likes": post.likes,
                "comments": post.comments_count,
                "shares": post.shares,
                "saves": post.saves,
                "engagement_rate": engagement_rate,
                "engagement_score": engagement_score,
            },
            sentiment={label.value: sentiment_counts.get(label.value, 0) for label in SentimentLabel},
            intents={intent.value: intent_counts.get(intent.value, 0) for intent in IntentCategory},
            topics=[
                {
                    "topic": topic.topic,
                    "frequency": topic.frequency,
                    "sentiment_avg": topic.sentiment_avg,
                    "key_keywords": topic.key_keywords,
                }
                for topic in nlp_result.topics
            ],
            comments_sample=_comments_sample(post, analyses),
            data_source=post.source,
        )
        insights_result = deps.insights.generate(context)

    # 8. Persist insights, finalize and cache -------------------------------------------
    warnings = [*post.warnings, *nlp_result.warnings, *insights_result.warnings]
    with timer.measure("finalize"), session_scope() as session:
        video = _get_video(session, video_id)
        if insights_result.insights:
            session.execute(
                insert(Insight),
                [
                    {
                        "video_id": video_id,
                        "type": item.type,
                        "title": item.title,
                        "description": item.description,
                        "impact_score": item.impact_score,
                        "source": "openai" if insights_result.source.startswith("openai") else "rules",
                    }
                    for item in insights_result.insights
                ],
            )
        video.current_step = TOTAL_STEPS
        video.status = VideoStatus.COMPLETED
        video.completed_at = datetime.now(timezone.utc)
        video.processing_stats = {
            "attempt": attempt,
            "data_source": post.source,
            "nlp_backend": nlp_result.backend,
            "insights_source": insights_result.source,
            "comments_analyzed": len(analyses),
            "duration_seconds": timer.total,
            "step_durations": dict(timer.durations),
            "nlp_durations": nlp_result.step_durations,
            "warnings": warnings,
        }

    with timer.measure("cache"):
        _write_cache(video_id, instagram_id, deps.cache, settings.cache_ttl_seconds)

    logger.info(
        "analysis_completed",
        extra={
            **log_ctx,
            "instagram_id": instagram_id,
            "source": post.source,
            "comments": len(analyses),
            "insights_source": insights_result.source,
            "duration_s": timer.total,
            "step_durations_s": timer.durations,
        },
    )
    return {"video_id": str(video_id), "status": "completed", "duration_seconds": timer.total}


# --------------------------------------------------------------------------- attempt handling
@dataclass
class AttemptResult:
    status: Literal["completed", "retry", "failed", "cancelled"]
    countdown: float = 0.0
    error: Exception | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, **self.detail}


def backoff_seconds(retries_done: int) -> float:
    """5s, 10s, 20s... capped at 60s, with jitter to avoid thundering herds."""
    return min(5 * 2**retries_done, 60) + random.uniform(0, 1)


def execute_attempt(
    video_id: uuid.UUID,
    *,
    attempt: int,
    max_retries: int,
    task_id: str | None = None,
    deps: PipelineDeps | None = None,
) -> AttemptResult:
    """Run one attempt and do all failure bookkeeping (status, retries, dead-letter queue)."""
    try:
        return AttemptResult("completed", detail=run_analysis(video_id, attempt=attempt, deps=deps))
    except AnalysisCancelled:
        logger.info("analysis_cancelled", extra={"video_id": str(video_id)})
        return AttemptResult("cancelled", detail={"video_id": str(video_id)})
    except PermanentAnalysisError as exc:
        _fail(video_id, str(exc), exc, attempt=attempt, task_id=task_id, dead_letter=False)
        return AttemptResult("failed", error=exc, detail={"video_id": str(video_id), "error": str(exc)})
    except SoftTimeLimitExceeded as exc:
        message = "Tempo limite de processamento excedido. Tente novamente mais tarde."
        _fail(video_id, message, exc, attempt=attempt, task_id=task_id, dead_letter=True)
        return AttemptResult("failed", error=exc, detail={"video_id": str(video_id), "error": message})
    except (TransientAnalysisError, OperationalError, DBAPIError) as exc:
        if isinstance(exc, DBAPIError) and not isinstance(exc, OperationalError) and not exc.connection_invalidated:
            return _unexpected(video_id, exc, attempt=attempt, task_id=task_id)
        if attempt <= max_retries:
            countdown = backoff_seconds(attempt - 1)
            logger.warning(
                "analysis_retry_scheduled",
                extra={
                    "video_id": str(video_id),
                    "attempt": attempt,
                    "countdown_s": round(countdown, 1),
                    "error": str(exc),
                },
            )
            _touch(video_id)
            return AttemptResult("retry", countdown=countdown, error=exc)
        message = (
            f"Instagram indisponível após {attempt} tentativas ({exc}). Tente novamente em alguns minutos."
            if isinstance(exc, TransientAnalysisError)
            else "Banco de dados indisponível durante a análise. Tente novamente em alguns minutos."
        )
        _fail(video_id, message, exc, attempt=attempt, task_id=task_id, dead_letter=True)
        return AttemptResult("failed", error=exc, detail={"video_id": str(video_id), "error": message})
    except Exception as exc:  # noqa: BLE001 - last line of defence for the job
        return _unexpected(video_id, exc, attempt=attempt, task_id=task_id)


def run_analysis_with_retries(video_id: uuid.UUID, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Inline (TASK_BACKEND=inline) equivalent of the Celery task, retries included."""
    max_retries = get_settings().task_max_retries
    attempt = 1
    while True:
        result = execute_attempt(video_id, attempt=attempt, max_retries=max_retries)
        if result.status != "retry":
            return result.as_dict()
        sleep(result.countdown)
        attempt += 1


def _unexpected(video_id: uuid.UUID, exc: Exception, *, attempt: int, task_id: str | None) -> AttemptResult:
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "analysis_unexpected_error",
        exc_info=exc,
        extra={"video_id": str(video_id), "error_id": error_id, "attempt": attempt},
    )
    message = f"Erro inesperado durante a análise (error_id: {error_id})."
    _fail(video_id, message, exc, attempt=attempt, task_id=task_id, dead_letter=True, error_id=error_id)
    return AttemptResult("failed", error=exc, detail={"video_id": str(video_id), "error": message})


def _fail(
    video_id: uuid.UUID,
    message: str,
    exc: BaseException,
    *,
    attempt: int,
    task_id: str | None,
    dead_letter: bool,
    error_id: str | None = None,
) -> None:
    logger.error(
        "analysis_failed",
        extra={"video_id": str(video_id), "attempt": attempt, "error": str(exc), "error_type": type(exc).__name__},
    )
    try:
        with session_scope() as session:
            session.execute(
                update(Video)
                .where(Video.id == video_id)
                .values(status=VideoStatus.FAILED, error_message=message[:2000], updated_at=utcnow())
            )
    except Exception:  # noqa: BLE001 - the DB itself may be the reason we are failing
        logger.exception("analysis_mark_failed_error", extra={"video_id": str(video_id)})
    if dead_letter:
        push_dead_letter(video_id, exc, attempts=attempt, task_id=task_id, error_id=error_id)


def push_dead_letter(
    video_id: uuid.UUID,
    exc: BaseException,
    *,
    attempts: int,
    task_id: str | None = None,
    error_id: str | None = None,
    redis_client: Any | None = None,
) -> None:
    """Keep failed jobs inspectable/replayable: `LRANGE dlq:process_video_analysis 0 -1`."""
    entry = {
        "video_id": str(video_id),
        "task_id": task_id,
        "error_id": error_id,
        "error_type": type(exc).__name__,
        "error": str(exc)[:1000],
        "attempts": attempts,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client = redis_client or get_sync_redis()
        pipe = client.pipeline()
        pipe.lpush(DLQ_KEY, json.dumps(entry))
        pipe.ltrim(DLQ_KEY, 0, DLQ_MAX_LENGTH - 1)
        pipe.execute()
    except Exception:  # noqa: BLE001
        logger.exception("dead_letter_push_failed", extra={"video_id": str(video_id)})


# --------------------------------------------------------------------------- helpers
def _get_video(session: Session, video_id: uuid.UUID) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise AnalysisCancelled(str(video_id))
    return video


def _advance(video_id: uuid.UUID, step: int) -> None:
    with session_scope() as session:
        result = session.execute(
            update(Video).where(Video.id == video_id).values(current_step=step, updated_at=utcnow())
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise AnalysisCancelled(str(video_id))
    logger.info("analysis_step", extra={"video_id": str(video_id), "step": step, "label": STEP_LABELS[step]})


def _touch(video_id: uuid.UUID) -> None:
    """Keep a retrying job from being flagged as stale."""
    try:
        with session_scope() as session:
            session.execute(update(Video).where(Video.id == video_id).values(current_step=0, updated_at=utcnow()))
    except Exception:  # noqa: BLE001
        logger.warning("analysis_touch_failed", extra={"video_id": str(video_id)})


def _apply_post_metadata(video: Video, post: ScrapedPost) -> None:
    video.caption = post.caption
    video.creator_username = (post.creator_username or "").lstrip("@")[:128] or None
    video.thumbnail_url = post.thumbnail_url
    video.published_at = post.published_at
    video.duration_sec = post.duration_sec
    video.media_type = post.media_type
    video.data_source = post.source


def _clear_previous_results(session: Session, video_id: uuid.UUID) -> None:
    """Idempotency: a retry or re-analysis replaces everything computed before."""
    comment_ids = select(Comment.id).where(Comment.video_id == video_id)
    session.execute(delete(SentimentAnalysis).where(SentimentAnalysis.comment_id.in_(comment_ids)))
    session.execute(delete(Comment).where(Comment.video_id == video_id))
    session.execute(delete(TopicAnalysis).where(TopicAnalysis.video_id == video_id))
    session.execute(delete(Insight).where(Insight.video_id == video_id))
    session.execute(delete(EngagementMetrics).where(EngagementMetrics.video_id == video_id))


def _comments_sample(post: ScrapedPost, analyses: list[Any], limit: int = 25) -> list[dict[str, Any]]:
    rows = [
        {
            "text": comment.text,
            "likes": comment.likes,
            "sentiment": analysis.sentiment.value,
            "intent": analysis.intent_category.value,
        }
        for comment, analysis in zip(post.comments, analyses)
        if analysis.sentiment is not SentimentLabel.SPAM
    ]
    rows.sort(key=lambda row: -row["likes"])
    return rows[:limit]


def _write_cache(video_id: uuid.UUID, instagram_id: str, cache: SyncCacheService, ttl_seconds: int) -> None:
    try:
        with session_scope() as session:
            data = load_analysis_sync(session, video_id)
            if data is None:
                return
            payload = data.model_dump(mode="json")
            cache.set(instagram_id, payload, ttl_seconds)
            upsert_cache_backup(session, instagram_id, payload, ttl_seconds)
    except Exception:  # noqa: BLE001 - the analysis is already committed; cache is best effort
        logger.exception("analysis_cache_write_failed", extra={"video_id": str(video_id)})
