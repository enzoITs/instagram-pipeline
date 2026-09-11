import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import pipeline
from app.database import session_scope
from app.models import Comment, EngagementMetrics, Insight, SentimentAnalysis, TopicAnalysis, Video, VideoStatus
from app.pipeline import (
    DLQ_KEY,
    AnalysisCancelled,
    StepTimer,
    backoff_seconds,
    execute_attempt,
    push_dead_letter,
    run_analysis,
    run_analysis_with_retries,
)
from app.services.cache_service import cache_key
from app.services.scraper_service import InstagramScraper, ScraperError


def failing_scraper(*errors):
    """Scraper factory raising the given errors in order, then delegating to the mock scraper."""
    remaining = list(errors)

    @contextmanager
    def factory():
        if remaining:
            error = remaining.pop(0)

            class Broken:
                def scrape(self, _url):
                    raise error

            yield Broken()
        else:
            with InstagramScraper() as scraper:
                yield scraper

    return factory


def count(model) -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_run_analysis_persists_everything(make_video, pipeline_deps, sync_redis):
    video = make_video(instagram_id="C8xYz12AbCd")
    result = run_analysis(video.id, deps=pipeline_deps())
    assert result["status"] == "completed"

    with session_scope() as session:
        stored = session.get(Video, video.id)
        assert stored.status is VideoStatus.COMPLETED
        assert stored.current_step == 8
        assert stored.completed_at is not None
        assert stored.creator_username == "loja.demo"
        assert stored.data_source == "mock"
        stats = stored.processing_stats
        assert stats["nlp_backend"] == "lexicon"
        assert stats["insights_source"] == "rules"
        assert {"scraping", "storage", "nlp", "nlp_storage", "calculations", "insights"} <= set(stats["step_durations"])
        assert stats["warnings"]
        metrics = session.scalars(select(EngagementMetrics)).one()
        assert metrics.engagement_rate > 0 and 0 <= metrics.engagement_score <= 10

    comments = count(Comment)
    assert comments == stats["comments_analyzed"] > 100
    assert count(SentimentAnalysis) == comments
    assert count(TopicAnalysis) > 0
    assert 3 <= count(Insight) <= 5

    cached = json.loads(sync_redis.get(cache_key("C8xYz12AbCd")))
    assert cached["video_id"] == str(video.id)
    assert sync_redis.ttl(cache_key("C8xYz12AbCd")) > 86000


def test_run_analysis_is_idempotent(make_video, pipeline_deps):
    video = make_video()
    run_analysis(video.id, deps=pipeline_deps())
    first = (count(Comment), count(SentimentAnalysis), count(TopicAnalysis), count(Insight), count(EngagementMetrics))
    run_analysis(video.id, attempt=2, deps=pipeline_deps())
    assert (count(Comment), count(SentimentAnalysis), count(TopicAnalysis), count(Insight), count(EngagementMetrics)) == first


def test_run_analysis_with_no_comments(make_video, pipeline_deps, monkeypatch):
    video = make_video()

    @contextmanager
    def empty_scraper():
        with InstagramScraper() as scraper:
            original = scraper.scrape

            def scrape(url):
                post = original(url)
                post.comments = []
                post.views = None
                return post

            scraper.scrape = scrape
            yield scraper

    run_analysis(video.id, deps=pipeline_deps(scraper_factory=empty_scraper))
    with session_scope() as session:
        assert session.get(Video, video.id).status is VideoStatus.COMPLETED
        assert session.scalars(select(EngagementMetrics)).one().engagement_score is None
    assert count(Insight) == 3


def test_transient_scraper_error_schedules_retry_then_fails_to_dlq(make_video, pipeline_deps, sync_redis):
    video = make_video(current_step=2)
    deps = pipeline_deps(scraper_factory=failing_scraper(*[ScraperError("HTTP 503", transient=True)] * 4))

    first = execute_attempt(video.id, attempt=1, max_retries=3, deps=deps)
    assert first.status == "retry"
    assert 5 <= first.countdown <= 6
    with session_scope() as session:
        assert session.get(Video, video.id).status is VideoStatus.PROCESSING
        assert session.get(Video, video.id).current_step == 0

    last = execute_attempt(video.id, attempt=4, max_retries=3, deps=deps, task_id="task-1")
    assert last.status == "failed"
    with session_scope() as session:
        stored = session.get(Video, video.id)
        assert stored.status is VideoStatus.FAILED
        assert "após 4 tentativas" in stored.error_message
    entry = json.loads(sync_redis.lindex(DLQ_KEY, 0))
    assert entry["video_id"] == str(video.id) and entry["task_id"] == "task-1" and entry["attempts"] == 4


def test_permanent_error_fails_without_retry_or_dlq(make_video, pipeline_deps, sync_redis):
    video = make_video()
    deps = pipeline_deps(scraper_factory=failing_scraper(ScraperError("post não encontrado", transient=False)))
    result = execute_attempt(video.id, attempt=1, max_retries=3, deps=deps)
    assert result.status == "failed"
    with session_scope() as session:
        assert "post não encontrado" in session.get(Video, video.id).error_message
    assert sync_redis.llen(DLQ_KEY) == 0


def test_deleted_video_cancels_quietly(pipeline_deps, db_engine):
    result = execute_attempt(uuid.uuid4(), attempt=1, max_retries=3, deps=pipeline_deps())
    assert result.status == "cancelled"


def test_video_deleted_mid_pipeline_is_cancelled(make_video, pipeline_deps, nlp):
    video = make_video()

    def delete_then_analyze(texts):  # the user deletes the analysis while NLP is running
        with session_scope() as session:
            session.delete(session.get(Video, video.id))
        return nlp.analyze(texts)

    deps = pipeline_deps(nlp=SimpleNamespace(analyze=delete_then_analyze))
    assert execute_attempt(video.id, attempt=1, max_retries=3, deps=deps).status == "cancelled"
    assert count(Comment) == 0  # cascade removed what the job had stored


def test_progress_update_on_missing_video_cancels(db_engine):
    with pytest.raises(AnalysisCancelled):
        pipeline._advance(uuid.uuid4(), 4)


def test_unexpected_error_gets_error_id_and_dlq(make_video, pipeline_deps, sync_redis):
    video = make_video()

    def explode(_texts):
        raise ValueError("bug")

    result = execute_attempt(video.id, attempt=1, max_retries=3, deps=pipeline_deps(nlp=SimpleNamespace(analyze=explode)))
    assert result.status == "failed"
    with session_scope() as session:
        message = session.get(Video, video.id).error_message
    assert "error_id" in message
    entry = json.loads(sync_redis.lindex(DLQ_KEY, 0))
    assert entry["error_type"] == "ValueError" and entry["error_id"] in message


def test_soft_time_limit_marks_failed(make_video, pipeline_deps, sync_redis):
    video = make_video()

    def slow(_texts):
        raise SoftTimeLimitExceeded()

    result = execute_attempt(video.id, attempt=1, max_retries=3, deps=pipeline_deps(nlp=SimpleNamespace(analyze=slow)))
    assert result.status == "failed"
    with session_scope() as session:
        assert "Tempo limite" in session.get(Video, video.id).error_message
    assert sync_redis.llen(DLQ_KEY) == 1


def test_database_outage_is_retried(make_video, pipeline_deps, monkeypatch):
    video = make_video()

    def db_down(_texts):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    deps = pipeline_deps(nlp=SimpleNamespace(analyze=db_down))
    assert execute_attempt(video.id, attempt=1, max_retries=3, deps=deps).status == "retry"
    final = execute_attempt(video.id, attempt=4, max_retries=3, deps=deps)
    assert final.status == "failed"
    with session_scope() as session:
        assert "Banco de dados indisponível" in session.get(Video, video.id).error_message


def test_inline_runner_retries_until_success(make_video, pipeline_deps, monkeypatch):
    video = make_video()
    deps = pipeline_deps(scraper_factory=failing_scraper(ScraperError("503", transient=True)))
    monkeypatch.setattr(pipeline, "default_deps", lambda: deps)
    sleeps = []
    result = run_analysis_with_retries(video.id, sleep=sleeps.append)
    assert result["status"] == "completed"
    assert len(sleeps) == 1


def test_cache_write_failure_does_not_fail_analysis(make_video, pipeline_deps, monkeypatch):
    video = make_video()

    def broken(*_args, **_kwargs):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(pipeline, "load_analysis_sync", broken)
    assert run_analysis(video.id, deps=pipeline_deps())["status"] == "completed"


def test_failure_bookkeeping_survives_database_and_redis_outages(monkeypatch, sync_redis, fake_server):
    @contextmanager
    def broken_scope():
        raise OperationalError("UPDATE", {}, Exception("down"))
        yield  # pragma: no cover

    monkeypatch.setattr(pipeline, "session_scope", broken_scope)
    fake_server.connected = False
    pipeline._fail(uuid.uuid4(), "msg", RuntimeError("x"), attempt=1, task_id=None, dead_letter=True)
    pipeline._touch(uuid.uuid4())
    push_dead_letter(uuid.uuid4(), RuntimeError("x"), attempts=1, redis_client=sync_redis)


def test_backoff_and_timer():
    assert 5 <= backoff_seconds(0) <= 6
    assert 20 <= backoff_seconds(2) <= 21
    assert 60 <= backoff_seconds(10) <= 61
    timer = StepTimer()
    with timer.measure("step"):
        pass
    assert timer.durations["step"] >= 0 and timer.total >= 0


def test_dead_letter_queue_is_capped(sync_redis, monkeypatch):
    monkeypatch.setattr(pipeline, "DLQ_MAX_LENGTH", 3)
    for _ in range(5):
        push_dead_letter(uuid.uuid4(), RuntimeError("x"), attempts=1, redis_client=sync_redis)
    assert sync_redis.llen(DLQ_KEY) == 3


def test_default_deps_wiring(sync_redis):
    deps = pipeline.default_deps()
    assert deps.scraper_factory is InstagramScraper
    assert deps.nlp.backend in {"lexicon", "transformers"}


# --------------------------------------------------------------------------- Celery task
def test_celery_task_retries_transient_errors_eagerly(make_video, pipeline_deps, monkeypatch, sync_redis):
    from app.tasks import process_video_analysis

    video = make_video()
    deps = pipeline_deps(
        scraper_factory=failing_scraper(ScraperError("503", transient=True), ScraperError("429", transient=True))
    )
    monkeypatch.setattr(pipeline, "default_deps", lambda: deps)
    result = process_video_analysis.apply(args=[str(video.id)])  # eager: retries run synchronously
    assert result.successful()
    assert result.result["status"] == "completed"
    with session_scope() as session:
        assert session.get(Video, video.id).processing_stats["attempt"] == 3


def test_celery_task_permanent_failure(make_video, pipeline_deps, monkeypatch, sync_redis):
    from app.tasks import process_video_analysis

    video = make_video()
    deps = pipeline_deps(scraper_factory=failing_scraper(ScraperError("privado", transient=False)))
    monkeypatch.setattr(pipeline, "default_deps", lambda: deps)
    result = process_video_analysis.apply(args=[str(video.id)])
    assert result.result["status"] == "failed"


def test_celery_configuration_and_worker_init(monkeypatch):
    from app import celery_app as celery_module

    conf = celery_module.celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_time_limit == 300 and conf.task_soft_time_limit == 270
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit
    assert conf.task_default_queue == "analysis"

    celery_module._setup_logging()
    monkeypatch.setattr(celery_module.settings, "nlp_torch_threads", None)
    monkeypatch.setattr(celery_module.celery_app.conf, "worker_concurrency", 1)
    celery_module._init_worker_process()  # lexicon backend in tests: fast
    assert celery_module.settings.nlp_torch_threads == celery_module.torch_threads_per_process(processes=1)

    assert celery_module.torch_threads_per_process(cores=12, processes=2) == 6
    assert celery_module.torch_threads_per_process(cores=4, processes=8) == 1
    assert celery_module.torch_threads_per_process(cores=8) == 1  # Celery default: one process per core
