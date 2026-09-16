"""FastAPI application: endpoints, middleware wiring and OpenAPI metadata."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_settings
from app.database import check_database, dispose_async_engine, get_session
from app.errors import APIError, register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.models import Video, VideoStatus, utcnow
from app.repository import (
    delete_cache_backup,
    get_cache_backup,
    load_analysis_async,
    recent_statement,
)
from app.schemas import (
    STEP_LABELS,
    TOTAL_STEPS,
    AnalysisErrorResponse,
    AnalysisProcessingResponse,
    AnalysisSuccessResponse,
    AnalyzeAcceptedResponse,
    AnalyzeCachedResponse,
    AnalyzeRequest,
    CacheStats,
    DeleteResponse,
    ErrorResponse,
    HealthResponse,
    Pagination,
    RecentItem,
    RecentResponse,
    with_at,
)
from app.services.cache_service import CacheService, create_async_redis

logger = logging.getLogger(__name__)
settings = get_settings()

API_DESCRIPTION = """
Analisa o engajamento de Reels e Posts do Instagram: coleta métricas e comentários,
classifica sentimento (5 categorias) e intenção (5 categorias) com NLP, extrai tópicos e
gera 3–5 insights com IA.

**Fluxo**
1. `POST /api/v1/analyze` com a URL → `202` com `video_id` (ou `200` direto do cache).
2. Consulte `GET /api/v1/analysis/{video_id}` a cada 2 s até `status` ser `success` ou `error`.

**Erros** seguem o envelope `{"status": "error", "error": {"code", "message", "error_id?"}}`.
`POST /analyze` é limitado a 10 requisições/minuto por IP (`429` + `Retry-After`).
"""

TAGS = [
    {"name": "analysis", "description": "Submissão, consulta e remoção de análises."},
    {"name": "system", "description": "Saúde do serviço."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings)
    app.state.redis = create_async_redis(settings)
    app.state.cache = CacheService(app.state.redis, settings.cache_ttl_seconds)
    logger.info(
        "api_started",
        extra={"version": __version__, "environment": settings.environment, "task_backend": settings.task_backend},
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await dispose_async_engine()
        logger.info("api_stopped")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=API_DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
)
register_exception_handlers(app)
# Order matters: the last added middleware is the outermost one.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Error-ID"],
)
app.add_middleware(RequestContextMiddleware)


def get_cache(request: Request) -> CacheService:
    return request.app.state.cache


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _is_stale(video: Video) -> bool:
    threshold = timedelta(seconds=settings.analysis_stale_after_seconds)
    return datetime.now(timezone.utc) - _as_utc(video.updated_at) > threshold


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Requisição inválida"},
    500: {"model": ErrorResponse, "description": "Erro interno (inclui `error_id`)"},
}


# --------------------------------------------------------------------------- endpoints
@app.post(
    "/api/v1/analyze",
    tags=["analysis"],
    summary="Iniciar análise de um vídeo",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalyzeAcceptedResponse,
    responses={
        200: {"model": AnalyzeCachedResponse, "description": "Resultado servido do cache (< 100 ms)"},
        **_ERROR_RESPONSES,
        429: {"model": ErrorResponse, "description": "Limite de 10 análises/minuto por IP excedido"},
        503: {"model": ErrorResponse, "description": "Fila de processamento indisponível"},
    },
)
async def analyze(
    payload: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache),
) -> Any:
    """Valida a URL, consulta o cache (Redis, com backup no PostgreSQL) e, se necessário,
    enfileira a análise em background. Nunca bloqueia aguardando o processamento."""
    instagram_id = payload.instagram_id

    cached = await cache.get(instagram_id)
    if cached is None:
        cached = await get_cache_backup(session, instagram_id)
        if cached is not None:
            await cache.set(instagram_id, cached)  # re-warm Redis from the durable backup
    if cached is not None:
        logger.info("analysis_cache_hit", extra={"instagram_id": instagram_id})
        return JSONResponse(
            {"status": "success", "video_id": cached["video_id"], "cached": True, "data": cached},
            status_code=status.HTTP_200_OK,
        )

    video = (await session.scalars(select(Video).where(Video.instagram_id == instagram_id))).first()
    if video is not None and video.status == VideoStatus.PROCESSING and not _is_stale(video):
        return AnalyzeAcceptedResponse(video_id=video.id, message="Análise já em andamento para este vídeo.")

    if video is None:
        video = Video(instagram_id=instagram_id, url=payload.url, media_type=payload.media_type)
        session.add(video)
    else:  # failed, stale or completed-but-expired: analyze again with fresh data
        video.url = payload.url
        video.status = VideoStatus.PROCESSING
        video.current_step = 0
        video.error_message = None
        video.completed_at = None
    task_id = str(uuid.uuid4()) if settings.task_backend == "celery" else None
    video.task_id = task_id
    video.updated_at = utcnow()
    try:
        await session.commit()
    except IntegrityError:
        # Two concurrent submissions of the same URL: the other one won, reuse its job.
        await session.rollback()
        existing = (await session.scalars(select(Video).where(Video.instagram_id == instagram_id))).one()
        return AnalyzeAcceptedResponse(video_id=existing.id, message="Análise já em andamento para este vídeo.")

    await _dispatch(video.id, task_id, session, background_tasks)
    logger.info("analysis_enqueued", extra={"video_id": str(video.id), "instagram_id": instagram_id})
    return AnalyzeAcceptedResponse(video_id=video.id)


async def _dispatch(
    video_id: uuid.UUID, task_id: str | None, session: AsyncSession, background_tasks: BackgroundTasks
) -> None:
    if settings.task_backend == "inline":
        from app.pipeline import run_analysis_with_retries  # worker dependencies, dev mode only

        background_tasks.add_task(run_analysis_with_retries, video_id)
        return

    from app.celery_app import TASK_NAME, celery_app

    try:
        await run_in_threadpool(celery_app.send_task, TASK_NAME, args=[str(video_id)], task_id=task_id)
    except Exception as exc:  # noqa: BLE001 - broker unreachable
        logger.error("analysis_enqueue_failed", extra={"video_id": str(video_id), "error": type(exc).__name__})
        video = await session.get(Video, video_id)
        if video is not None:
            video.status = VideoStatus.FAILED
            video.error_message = "Fila de processamento indisponível."
            await session.commit()
        raise APIError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "queue_unavailable",
            "Fila de processamento indisponível. Tente novamente em instantes.",
            headers={"Retry-After": "30"},
        ) from exc


@app.get(
    "/api/v1/analysis/{video_id}",
    tags=["analysis"],
    summary="Consultar status/resultado de uma análise",
    response_model=AnalysisSuccessResponse | AnalysisProcessingResponse | AnalysisErrorResponse,
    responses={404: {"model": ErrorResponse, "description": "Análise não encontrada"}, **_ERROR_RESPONSES},
)
async def get_analysis(
    video_id: uuid.UUID = Path(..., description="ID retornado por POST /api/v1/analyze"),
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache),
) -> Any:
    """`processing` enquanto a análise roda (com etapa atual), `success` com o resultado completo
    ou `error` com a mensagem de falha. Resultados concluídos são servidos do cache."""
    video = await session.get(Video, video_id)
    if video is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Análise não encontrada")

    if video.status == VideoStatus.PROCESSING:
        if _is_stale(video):
            video.status = VideoStatus.FAILED
            video.error_message = "A análise não respondeu a tempo e foi interrompida. Tente novamente."
            await session.commit()
            logger.warning("analysis_marked_stale", extra={"video_id": str(video_id)})
        else:
            step = int(video.current_step or 0)
            return AnalysisProcessingResponse(
                video_id=video.id,
                message=f"Análise em andamento... (etapa {step}/{TOTAL_STEPS}: {STEP_LABELS.get(step, '')})",
                step=step,
            )

    if video.status == VideoStatus.FAILED:
        return AnalysisErrorResponse(video_id=video.id, message=video.error_message or "Falha na análise.")

    cached = await cache.get(video.instagram_id)
    if cached is not None and cached.get("video_id") == str(video.id):
        return JSONResponse({"status": "success", "data": cached})

    data = await load_analysis_async(session, video.id)
    if data is None:  # deleted concurrently
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Análise não encontrada")
    payload = data.model_dump(mode="json")
    await cache.set(video.instagram_id, payload)
    return JSONResponse({"status": "success", "data": payload})


@app.get(
    "/api/v1/recent",
    tags=["analysis"],
    summary="Listar análises recentes",
    response_model=RecentResponse,
    responses=_ERROR_RESPONSES,
)
async def recent(
    limit: int = Query(10, ge=1, le=100, description="Itens por página (máx. 100)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    session: AsyncSession = Depends(get_session),
) -> RecentResponse:
    """Análises ordenadas da mais recente para a mais antiga."""
    total = int(await session.scalar(select(func.count()).select_from(Video)) or 0)
    rows = (await session.execute(recent_statement(limit, offset))).all()
    return RecentResponse(
        data=[
            RecentItem.model_validate({**row._mapping, "creator_username": with_at(row.creator_username)})
            for row in rows
        ],
        pagination=Pagination(total=total, limit=limit, offset=offset, page=offset // limit + 1),
    )


@app.delete(
    "/api/v1/analysis/{video_id}",
    tags=["analysis"],
    summary="Remover uma análise",
    response_model=DeleteResponse,
    responses={404: {"model": ErrorResponse, "description": "Análise não encontrada"}, **_ERROR_RESPONSES},
)
async def delete_analysis(
    video_id: uuid.UUID = Path(...),
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache),
) -> DeleteResponse:
    """Remove o vídeo e todos os dados relacionados (cascade) e invalida o cache."""
    video = await session.get(Video, video_id)
    if video is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Análise não encontrada")
    instagram_id, task_id, was_processing = video.instagram_id, video.task_id, video.status == VideoStatus.PROCESSING

    await delete_cache_backup(session, instagram_id)
    await session.execute(delete(Video).where(Video.id == video_id))
    await session.commit()
    await cache.delete(instagram_id)

    if was_processing and task_id and settings.task_backend == "celery":
        await _revoke(task_id)
    logger.info("analysis_deleted", extra={"video_id": str(video_id), "instagram_id": instagram_id})
    return DeleteResponse()


async def _revoke(task_id: str) -> None:
    """Best effort: a running job also notices the deleted row and stops by itself."""
    from app.celery_app import celery_app

    try:
        await asyncio.wait_for(run_in_threadpool(celery_app.control.revoke, task_id), timeout=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_revoke_failed", extra={"task_id": task_id, "error": type(exc).__name__})


@app.get(
    "/health",
    tags=["system"],
    summary="Health check",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Dependência indisponível"}},
)
async def health(response: Response, cache: CacheService = Depends(get_cache)) -> HealthResponse:
    """Verifica PostgreSQL (`SELECT 1`) e Redis (`PING`), cada um com timeout de 2 s."""
    database_ok, redis_ok = await asyncio.gather(check_database(timeout=2.0), cache.ping(timeout=2.0))
    healthy = database_ok and redis_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "degraded",
        database="connected" if database_ok else "disconnected",
        redis="connected" if redis_ok else "disconnected",
        timestamp=datetime.now(timezone.utc),
        version=__version__,
        cache=CacheStats(**await cache.stats()) if redis_ok else None,
    )


# --------------------------------------------------------------------------- OpenAPI
def custom_openapi() -> dict[str, Any]:
    """Document request-validation errors as 400 (what the API returns), not FastAPI's 422."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            responses = operation.get("responses", {})
            if responses.pop("422", None) is not None and "400" not in responses:
                responses["400"] = {
                    "description": "Requisição inválida",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                }
    for unused in ("HTTPValidationError", "ValidationError"):
        schema.get("components", {}).get("schemas", {}).pop(unused, None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
