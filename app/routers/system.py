"""Health check and CSV export."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app import scheduler as scheduler_module
from app.analyzer.models import Video
from app.config import get_settings
from app.database import check_database, get_db
from app.models import Account, Media, MetricSnapshot
from app.routers.accounts import get_account
from app.schemas import HealthOut
from app.security import require_session, session_is_valid

router = APIRouter(tags=["system"])

EXPORT_COLUMNS = [
    "collected_at",
    "ig_media_id",
    "permalink",
    "media_type",
    "media_product_type",
    "published_at",
    "likes",
    "comments",
    "saved",
    "shares",
    "reach",
    "views",
    "total_interactions",
    "engagement_rate",
    "engagement_basis",
    "caption",
]


@router.get("/api/health", response_model=HealthOut, summary="Service health and config")
async def health(request: Request, response: Response, db: Session = Depends(get_db)) -> HealthOut:
    """Liveness plus, for a signed-in caller, the configuration detail.

    The endpoint stays reachable without a session so an uptime probe works,
    but an anonymous caller only learns whether the service is up — not how it
    is configured, how many accounts it holds or how busy it is. A dependency
    that is down answers 503, which is what container health checks read.
    """
    settings = get_settings()
    database_ok = await check_database(timeout=2.0)
    database = "ok" if database_ok else "error"

    cache = getattr(request.app.state, "cache", None)
    redis_ok = bool(cache is not None and await cache.ping(timeout=2.0))

    status_value = "ok" if database_ok and redis_ok else "degraded"
    if status_value == "degraded":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    if not session_is_valid(request, settings):
        return HealthOut(
            status=status_value,
            auth_required=True,
            database=database,
            redis="ok" if redis_ok else "error",
        )

    connected = db.scalar(select(func.count(Account.id)).where(Account.is_active.is_(True))) or 0
    analyses = db.scalar(select(func.count(Video.id))) or 0
    stats = await cache.stats() if redis_ok and cache is not None else {}

    return HealthOut(
        status=status_value,
        auth_required=settings.auth_enabled,
        environment=settings.environment,
        version=__version__,
        database=database,
        redis="ok" if redis_ok else "error",
        meta_configured=settings.is_configured,
        login_flow=settings.meta_login_flow,
        graph_api_version=settings.graph_api_version,
        scheduler_running=settings.enable_scheduler,
        connected_accounts=connected,
        analyses=analyses,
        cache_hit_ratio=stats.get("hit_ratio"),
        next_collection_at=await run_in_threadpool(scheduler_module.next_collection_time),
    )


@router.get("/health", include_in_schema=False)
async def health_alias(request: Request, response: Response, db: Session = Depends(get_db)) -> HealthOut:
    """Alias kept for container health checks and the former analyzer clients."""
    return await health(request, response, db)


@router.get(
    "/api/accounts/{account_id}/export.csv",
    summary="Download the full metric history as CSV",
    response_class=StreamingResponse,
    dependencies=[Depends(require_session)],
)
def export_csv(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    latest_only: bool = Query(default=False, description="Export only the newest snapshot per post"),
) -> StreamingResponse:
    stmt = (
        select(MetricSnapshot, Media)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id)
        .order_by(MetricSnapshot.collected_at.desc(), Media.id)
        .execution_options(yield_per=500)
    )
    filename = f"instagram-metrics-{account.username or account.ig_user_id}.csv"
    return StreamingResponse(
        _csv_rows(db, stmt, latest_only=latest_only),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_rows(db: Session, stmt, *, latest_only: bool) -> Iterator[str]:  # noqa: ANN001
    """Yield the export one chunk at a time.

    Years of history can be far more rows than are comfortable to hold in
    memory, so the query streams and each line is handed to the client as it is
    produced instead of being assembled into a single string.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    writer.writerow(EXPORT_COLUMNS)
    yield flush()

    seen: set[int] = set()
    for snapshot, media in db.execute(stmt):
        if latest_only:
            if media.id in seen:
                continue
            seen.add(media.id)
        writer.writerow(
            [
                _iso(snapshot.collected_at),
                _text(media.ig_media_id),
                _text(media.permalink),
                _text(media.media_type),
                _text(media.media_product_type),
                _iso(media.timestamp),
                _num(snapshot.likes),
                _num(snapshot.comments),
                _num(snapshot.saved),
                _num(snapshot.shares),
                _num(snapshot.reach),
                _num(snapshot.views),
                _num(snapshot.total_interactions),
                _num(snapshot.engagement_rate),
                _text(snapshot.engagement_basis),
                _text((media.caption or "").replace("\n", " ").strip()[:500]),
            ]
        )
        yield flush()


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _num(value: int | float | None) -> str:
    return "" if value is None else str(value)


# Characters a spreadsheet treats as the start of a formula. A caption is
# attacker-controlled text, so prefixing it keeps the export from executing
# anything when opened in Excel, LibreOffice or Sheets.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text(value: str | None) -> str:
    """Return a cell value that cannot be interpreted as a formula."""
    if not value:
        return ""
    if value.startswith(_FORMULA_PREFIXES):
        return f"\t{value}"
    return value
