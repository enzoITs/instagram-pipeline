"""Health check and CSV export."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.config import get_settings
from app.database import get_db
from app.models import Account, Media, MetricSnapshot
from app.routers.accounts import get_account
from app.schemas import HealthOut

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
def health(db: Session = Depends(get_db)) -> HealthOut:
    settings = get_settings()
    try:
        db.execute(select(1))
        database = "ok"
    except Exception as exc:  # pragma: no cover - only on a broken DB
        database = f"error: {exc}"

    connected = db.scalar(
        select(func.count(Account.id)).where(Account.is_active.is_(True))
    ) or 0

    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        environment=settings.environment,
        database=database,
        meta_configured=settings.is_configured,
        login_flow=settings.meta_login_flow,
        graph_api_version=settings.graph_api_version,
        scheduler_running=scheduler_module.is_running(),
        connected_accounts=connected,
        next_collection_at=scheduler_module.next_collection_time(),
    )


@router.get(
    "/api/accounts/{account_id}/export.csv",
    summary="Download the full metric history as CSV",
    response_class=StreamingResponse,
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
    )
    rows = db.execute(stmt).all()

    if latest_only:
        seen: set[int] = set()
        filtered = []
        for snapshot, media in rows:
            if media.id in seen:
                continue
            seen.add(media.id)
            filtered.append((snapshot, media))
        rows = filtered

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for snapshot, media in rows:
        writer.writerow(
            [
                _iso(snapshot.collected_at),
                media.ig_media_id,
                media.permalink or "",
                media.media_type or "",
                media.media_product_type or "",
                _iso(media.timestamp),
                _num(snapshot.likes),
                _num(snapshot.comments),
                _num(snapshot.saved),
                _num(snapshot.shares),
                _num(snapshot.reach),
                _num(snapshot.views),
                _num(snapshot.total_interactions),
                _num(snapshot.engagement_rate),
                snapshot.engagement_basis or "",
                (media.caption or "").replace("\n", " ").strip()[:500],
            ]
        )

    buffer.seek(0)
    filename = f"instagram-metrics-{account.username or account.ig_user_id}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _num(value: int | float | None) -> str:
    return "" if value is None else str(value)
