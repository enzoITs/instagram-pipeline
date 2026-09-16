"""Link analysis endpoints: submit a URL, follow the job, read or drop the result."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzer.models import Video, VideoStatus, utcnow
from app.analyzer.repository import (
    delete_cache_backup,
    get_cache_backup,
    load_analysis_async,
    recent_statement,
)
from app.analyzer.schemas import (
    STEP_LABELS,
    TOTAL_STEPS,
    AnalysisErrorResponse,
    AnalysisProcessingResponse,
    AnalysisSuccessResponse,
    AnalyzeAcceptedResponse,
    AnalyzeCachedResponse,
    AnalyzeRequest,
    DeleteResponse,
    ErrorResponse,
    Pagination,
    RecentItem,
    RecentResponse,
    TrackingRequest,
    TrackingResponse,
    with_at,
)
from app.analyzer.services.cache_service import CacheService
from app.config import get_settings
from app.database import get_session
from app.errors import APIError
from app.security import require_session

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/api/v1",
    tags=["analysis"],
    dependencies=[Depends(require_session)],
)

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Requisição inválida"},
    401: {"model": ErrorResponse, "description": "Sessão necessária"},
    500: {"model": ErrorResponse, "description": "Erro interno (inclui `error_id`)"},
}


def get_cache(request: Request) -> CacheService:
    return request.app.state.cache


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _is_stale(video: Video) -> bool:
    threshold = timedelta(seconds=get_settings().analysis_stale_after_seconds)
    return datetime.now(timezone.utc) - _as_utc(video.updated_at) > threshold


@router.post(
    "/analyze",
    summary="Iniciar análise de um vídeo",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalyzeAcceptedResponse,
    responses={
        200: {"model": AnalyzeCachedResponse, "description": "Resultado servido do cache (< 100 ms)"},
        **_ERROR_RESPONSES,
        429: {"model": ErrorResponse, "description": "Limite de análises por minuto excedido"},
        503: {"model": ErrorResponse, "description": "Fila de processamento indisponível"},
    },
)
async def analyze(
    payload: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache),
) -> Any:
    """Valida a URL, consulta o cache (Redis, com backup no banco) e, se necessário,
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
    task_id = str(uuid.uuid4()) if get_settings().task_backend == "celery" else None
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
    if get_settings().task_backend == "inline":
        from app.analyzer.pipeline import run_analysis_with_retries  # worker dependencies

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


@router.get(
    "/analysis/{video_id}",
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

    cached = await cache.get(video.instagram_id, count_stats=False)
    if cached is not None and cached.get("video_id") == str(video.id):
        return JSONResponse({"status": "success", "data": cached})

    data = await load_analysis_async(session, video.id)
    if data is None:  # deleted concurrently
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Análise não encontrada")
    payload = data.model_dump(mode="json")
    await cache.set(video.instagram_id, payload)
    return JSONResponse({"status": "success", "data": payload})


@router.get(
    "/recent",
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


@router.put(
    "/analysis/{video_id}/tracking",
    summary="Acompanhar (ou parar de acompanhar) a evolução deste link",
    response_model=TrackingResponse,
    responses={404: {"model": ErrorResponse, "description": "Análise não encontrada"}, **_ERROR_RESPONSES},
)
async def set_tracking(
    payload: TrackingRequest,
    video_id: uuid.UUID = Path(...),
    session: AsyncSession = Depends(get_session),
) -> TrackingResponse:
    """Um link acompanhado recebe uma nova coleta de métricas periodicamente
    (apenas números, sem comentários nem NLP), formando um histórico como o das
    contas conectadas."""
    video = await session.get(Video, video_id)
    if video is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Análise não encontrada")
    video.tracked = payload.tracked
    await session.commit()
    logger.info("analysis_tracking_changed", extra={"video_id": str(video_id), "tracked": payload.tracked})
    return TrackingResponse(video_id=video.id, tracked=video.tracked)


@router.delete(
    "/analysis/{video_id}",
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
    instagram_id, task_id = video.instagram_id, video.task_id
    was_processing = video.status == VideoStatus.PROCESSING

    await delete_cache_backup(session, instagram_id)
    await session.execute(delete(Video).where(Video.id == video_id))
    await session.commit()
    await cache.delete(instagram_id)

    if was_processing and task_id and get_settings().task_backend == "celery":
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
