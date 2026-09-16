import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.main as main_module
import app.routers.analysis as analysis_module
import app.routers.system as system_module
from app.analyzer.models import Comment, Video, VideoStatus
from app.analyzer.pipeline import run_analysis
from app.analyzer.services.cache_service import cache_key
from app.database import session_scope
from app.middleware import client_ip

REEL = "https://www.instagram.com/reel/C8xYz12AbCd/"


def seed_completed(make_video, pipeline_deps, **overrides) -> Video:
    video = make_video(**overrides)
    run_analysis(video.id, deps=pipeline_deps())
    return video


# --------------------------------------------------------------------------- POST /analyze
async def test_analyze_rejects_invalid_url(client):
    response = await client.post("/api/v1/analyze", json={"url": "https://www.youtube.com/watch?v=1"})
    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "invalid_url"
    assert body["error"]["message"].startswith("URL inválida")


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"link": REEL}, {"url": 123}])
async def test_analyze_rejects_malformed_bodies(client, payload):
    response = await client.post("/api/v1/analyze", json=payload)
    assert response.status_code == 400
    assert response.json()["status"] == "error"


async def test_analyze_accepts_and_processes_in_background(client, monkeypatch):
    # Keep the job queued so the response can be inspected before processing.
    dispatched = []
    monkeypatch.setattr(analysis_module, "_dispatch", lambda video_id, *_args: _record(dispatched, video_id))

    started = time.perf_counter()
    response = await client.post("/api/v1/analyze", json={"url": "instagram.com/reel/C8xYz12AbCd/?igsh=1"})
    assert time.perf_counter() - started < 0.5
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["message"].startswith("Análise iniciada")
    assert dispatched == [uuid.UUID(body["video_id"])]

    with session_scope() as session:
        video = session.get(Video, uuid.UUID(body["video_id"]))
        assert video.instagram_id == "C8xYz12AbCd"
        assert video.url == REEL
        assert video.status is VideoStatus.PROCESSING

    status = await client.get(f"/api/v1/analysis/{body['video_id']}")
    assert status.json() == {
        "status": "processing",
        "video_id": body["video_id"],
        "message": "Análise em andamento... (etapa 0/8: Na fila de processamento)",
        "step": 0,
        "total_steps": 8,
    }

    duplicate = await client.post("/api/v1/analyze", json={"url": REEL})
    assert duplicate.status_code == 202
    assert duplicate.json()["video_id"] == body["video_id"]
    assert "já em andamento" in duplicate.json()["message"]
    assert len(dispatched) == 1


async def _record(bucket, video_id):
    bucket.append(video_id)


async def test_analyze_serves_cache_hits_fast(client, make_video, pipeline_deps):
    video = seed_completed(make_video, pipeline_deps, instagram_id="C8xYz12AbCd")
    started = time.perf_counter()
    response = await client.post("/api/v1/analyze", json={"url": REEL})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is True
    assert body["video_id"] == str(video.id)
    assert body["data"]["video"]["instagram_id"] == "C8xYz12AbCd"
    assert elapsed < 0.1


async def test_analyze_uses_postgres_backup_when_redis_was_flushed(client, make_video, pipeline_deps, async_redis):
    seed_completed(make_video, pipeline_deps, instagram_id="C8xYz12AbCd")
    await async_redis.flushall()
    response = await client.post("/api/v1/analyze", json={"url": REEL})
    assert response.status_code == 200
    assert await async_redis.exists(cache_key("C8xYz12AbCd"))  # Redis re-warmed


async def test_analyze_reprocesses_failed_and_stale_videos(client, make_video, monkeypatch):
    dispatched = []
    monkeypatch.setattr(analysis_module, "_dispatch", lambda video_id, *_args: _record(dispatched, video_id))
    failed = make_video(instagram_id="FAILED1", status=VideoStatus.FAILED, error_message="boom")
    stale = make_video(instagram_id="STALE01", updated_at=datetime.now(timezone.utc) - timedelta(hours=2))

    for video in (failed, stale):
        response = await client.post("/api/v1/analyze", json={"url": video.url})
        assert response.status_code == 202
        assert response.json()["video_id"] == str(video.id)
    assert dispatched == [failed.id, stale.id]
    with session_scope() as session:
        refreshed = session.get(Video, failed.id)
        assert refreshed.status is VideoStatus.PROCESSING and refreshed.error_message is None


async def test_analyze_enqueues_celery_task(client, monkeypatch, settings):
    from app.celery_app import TASK_NAME, celery_app

    sent = []
    monkeypatch.setattr(settings, "task_backend", "celery")
    monkeypatch.setattr(celery_app, "send_task", lambda name, args, task_id: sent.append((name, args, task_id)))
    response = await client.post("/api/v1/analyze", json={"url": REEL})
    assert response.status_code == 202
    video_id = response.json()["video_id"]
    assert sent[0][0] == TASK_NAME and sent[0][1] == [video_id]
    with session_scope() as session:
        assert session.get(Video, uuid.UUID(video_id)).task_id == sent[0][2]


async def test_analyze_returns_503_when_queue_is_down(client, monkeypatch, settings):
    from app.celery_app import celery_app

    def broken(*_args, **_kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(settings, "task_backend", "celery")
    monkeypatch.setattr(celery_app, "send_task", broken)
    response = await client.post("/api/v1/analyze", json={"url": REEL})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "queue_unavailable"
    assert response.headers["retry-after"] == "30"
    with session_scope() as session:
        video = session.scalars(select(Video)).one()
        assert video.status is VideoStatus.FAILED


async def test_rate_limit_blocks_11th_request(client, monkeypatch):
    monkeypatch.setattr(analysis_module, "_dispatch", lambda video_id, *_args: _record([], video_id))
    monkeypatch.setattr("app.middleware.time.time", lambda: 1_000_040.0)  # 20s into a window, deterministic
    statuses = []
    for index in range(11):
        response = await client.post("/api/v1/analyze", json={"url": f"https://www.instagram.com/p/RATE{index:04d}/"})
        statuses.append(response.status_code)
    assert statuses[:10] == [202] * 10
    assert statuses[10] == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(response.headers["retry-after"]) == 40
    assert response.headers["x-ratelimit-remaining"] == "0"
    # Other routes are not limited
    assert (await client.get("/api/v1/recent")).status_code == 200


async def test_rate_limit_fails_open_without_redis(client, monkeypatch, fake_server):
    monkeypatch.setattr(analysis_module, "_dispatch", lambda video_id, *_args: _record([], video_id))
    fake_server.connected = False
    response = await client.post("/api/v1/analyze", json={"url": REEL})
    assert response.status_code == 202


# --------------------------------------------------------------------------- GET /analysis/{id}
async def test_get_analysis_success_payload(client, make_video, pipeline_deps, async_redis):
    video = seed_completed(make_video, pipeline_deps, instagram_id="C8xYz12AbCd")
    await async_redis.flushall()  # force the database path first

    response = await client.get(f"/api/v1/analysis/{video.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    data = body["data"]
    assert data["video"]["creator_username"] == "@loja.demo"
    assert set(data["metrics"]) == {
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
        "reach",
        "engagement_rate",
        "engagement_basis",
        "engagement_score",
    }
    assert data["metrics"]["engagement_basis"] == "views"  # public post: no reach available
    assert data["video"]["owned"] is False
    assert data["tracked"] is False
    assert len(data["history"]) == 1  # the analysis itself is the first point
    assert 0 <= data["metrics"]["engagement_score"] <= 10
    assert sum(data["sentiment"].values()) == data["processing"]["comments_analyzed"]
    assert sum(data["intents"].values()) == data["processing"]["comments_analyzed"]
    assert 3 <= len(data["insights"]) <= 5
    assert data["top_topics"] and data["top_topics"][0]["frequency"] >= data["top_topics"][-1]["frequency"]
    sample = data["sentiment_comments_sample"]
    assert 0 < len(sample) <= 50
    assert sample[0]["author_username"].startswith("@")
    assert {"sentiment", "confidence", "intent_category", "key_phrases"} == set(sample[0]["sentiment_analysis"])
    assert data["processing"]["data_source"] == "mock"

    assert await async_redis.exists(cache_key("C8xYz12AbCd"))  # cached after the DB read
    cached = await client.get(f"/api/v1/analysis/{video.id}")
    assert cached.json() == body


async def test_get_analysis_processing_step_message(client, make_video):
    video = make_video(current_step=3)
    body = (await client.get(f"/api/v1/analysis/{video.id}")).json()
    assert body["status"] == "processing"
    assert body["message"] == "Análise em andamento... (etapa 3/8: Salvando métricas e comentários)"
    assert body["step"] == 3


async def test_get_analysis_failed(client, make_video):
    video = make_video(status=VideoStatus.FAILED, error_message="post privado")
    assert (await client.get(f"/api/v1/analysis/{video.id}")).json() == {
        "status": "error",
        "video_id": str(video.id),
        "message": "post privado",
    }


async def test_get_analysis_marks_stale_jobs_failed(client, make_video):
    video = make_video(current_step=2)
    with session_scope() as session:
        session.execute(
            update(Video).where(Video.id == video.id).values(updated_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
    body = (await client.get(f"/api/v1/analysis/{video.id}")).json()
    assert body["status"] == "error"
    assert "não respondeu a tempo" in body["message"]


async def test_get_analysis_not_found_and_invalid_id(client):
    missing = await client.get(f"/api/v1/analysis/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"] == {"code": "not_found", "message": "Análise não encontrada"}
    assert missing.json()["detail"] == "Análise não encontrada"  # FastAPI key, kept for compatibility
    assert missing.json()["detail"] == "Análise não encontrada"  # FastAPI's usual key, kept for compatibility
    invalid = await client.get("/api/v1/analysis/not-a-uuid")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- GET /recent
async def test_recent_pagination(client, make_video):
    now = datetime.now(timezone.utc)
    for index in range(12):
        make_video(instagram_id=f"RECENT{index:02d}", created_at=now - timedelta(minutes=index), creator_username="brand")

    first = (await client.get("/api/v1/recent", params={"limit": 5})).json()
    assert first["pagination"] == {"total": 12, "limit": 5, "offset": 0, "page": 1}
    assert [item["instagram_id"] for item in first["data"]] == [f"RECENT{index:02d}" for index in range(5)]
    assert first["data"][0]["creator_username"] == "@brand"
    assert first["data"][0]["url"] == "https://www.instagram.com/reel/RECENT00/"
    assert first["data"][0]["engagement_score"] is None

    third = (await client.get("/api/v1/recent", params={"limit": 5, "offset": 10})).json()
    assert third["pagination"]["page"] == 3
    assert len(third["data"]) == 2

    default = (await client.get("/api/v1/recent")).json()
    assert default["pagination"]["limit"] == 10


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"limit": "abc"}])
async def test_recent_rejects_invalid_pagination(client, params):
    response = await client.get("/api/v1/recent", params=params)
    assert response.status_code == 400


# --------------------------------------------------------------------------- DELETE
async def test_delete_analysis_cascades_and_clears_cache(client, make_video, pipeline_deps, async_redis):
    video = seed_completed(make_video, pipeline_deps, instagram_id="C8xYz12AbCd")
    assert await async_redis.exists(cache_key("C8xYz12AbCd"))

    response = await client.delete(f"/api/v1/analysis/{video.id}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "message": "Análise removida com sucesso"}
    assert not await async_redis.exists(cache_key("C8xYz12AbCd"))
    with session_scope() as session:
        assert session.scalars(select(Comment)).first() is None
    assert (await client.get(f"/api/v1/analysis/{video.id}")).status_code == 404
    assert (await client.delete(f"/api/v1/analysis/{video.id}")).status_code == 404
    # The backup is gone too, so a new submission starts a fresh analysis.
    assert (await client.post("/api/v1/analyze", json={"url": REEL})).status_code == 202


async def test_delete_processing_video_revokes_celery_task(client, make_video, monkeypatch, settings):
    from app.celery_app import celery_app

    revoked = []
    monkeypatch.setattr(settings, "task_backend", "celery")
    monkeypatch.setattr(celery_app.control, "revoke", lambda task_id: revoked.append(task_id))
    video = make_video(task_id="task-123")
    assert (await client.delete(f"/api/v1/analysis/{video.id}")).status_code == 200
    assert revoked == ["task-123"]


async def test_delete_survives_revoke_failure(client, make_video, monkeypatch, settings):
    from app.celery_app import celery_app

    def broken(_task_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(settings, "task_backend", "celery")
    monkeypatch.setattr(celery_app.control, "revoke", broken)
    video = make_video(task_id="task-123")
    assert (await client.delete(f"/api/v1/analysis/{video.id}")).status_code == 200


# --------------------------------------------------------------------------- health, docs, errors, middleware
async def test_health_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok" and body["redis"] == "ok"
    assert body["cache_hit_ratio"] is None


async def test_health_degraded(client, monkeypatch, fake_server):
    async def db_down(timeout: float) -> bool:
        return False

    monkeypatch.setattr(system_module, "check_database", db_down)
    fake_server.connected = False
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "error"
    assert response.json()["redis"] == "error"


async def test_openapi_documents_endpoints_and_400(client):
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert {"/api/v1/analyze", "/api/v1/analysis/{video_id}", "/api/v1/recent", "/api/health"} <= set(paths)
    analyze_responses = paths["/api/v1/analyze"]["post"]["responses"]
    assert {"200", "202", "400", "429", "500", "503"} <= set(analyze_responses)
    assert "422" not in analyze_responses
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert (await client.get("/docs")).status_code == 200
    assert main_module.custom_openapi() is main_module.app.openapi_schema  # generated once, then cached


async def test_unhandled_errors_return_error_id(client, monkeypatch):
    async def explode(*_args, **_kwargs):
        raise RuntimeError("database password=hunter2 leaked?")

    monkeypatch.setattr(analysis_module, "load_analysis_async", explode)
    with session_scope() as session:
        video = Video(instagram_id="BOOM123", url="https://www.instagram.com/reel/BOOM123/", status=VideoStatus.COMPLETED)
        session.add(video)
    response = await client.get(f"/api/v1/analysis/{video.id}")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert len(error["error_id"]) == 12
    assert response.headers["x-error-id"] == error["error_id"]
    assert "hunter2" not in response.text


async def test_unknown_route_uses_error_envelope(client):
    response = await client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json() == {
        "status": "error",
        "error": {"code": "not_found", "message": "Recurso não encontrado"},
        "detail": "Recurso não encontrado",
    }
    method = await client.put("/api/v1/recent")
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "method_not_allowed"


async def test_cors_and_request_id_headers(client):
    preflight = await client.options(
        "/api/v1/analyze",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    blocked = await client.options(
        "/api/v1/analyze", headers={"Origin": "http://evil.com", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in blocked.headers

    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    assert float(response.headers["x-process-time-ms"]) >= 0
    generated = await client.get("/health", headers={"X-Request-ID": "bad id with spaces!"})
    assert len(generated.headers["x-request-id"]) == 32


def test_client_ip_resolution():
    forwarded = {
        "client": ("10.0.0.5", 1234),
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.9"), (b"x-real-ip", b"")],
    }
    assert client_ip(forwarded, trust_proxy_headers=False) == "10.0.0.5"  # headers ignored unless trusted
    assert client_ip(forwarded, trust_proxy_headers=True) == "203.0.113.9"  # right-most entry wins
    real_ip = {"client": ("10.0.0.5", 1234), "headers": [(b"x-real-ip", b"198.51.100.7")]}
    assert client_ip(real_ip, trust_proxy_headers=True) == "198.51.100.7"
    assert client_ip({"headers": []}, trust_proxy_headers=True) == "unknown"


async def test_lifespan_initializes_and_closes_resources():
    from app.main import app, lifespan

    async with lifespan(app):
        assert app.state.cache.client is app.state.redis
