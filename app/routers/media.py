"""Endpoints for browsing collected posts and their metric history."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Media, MetricSnapshot
from app.routers.accounts import get_account
from app.schemas import MediaDetail, MediaPage, MediaWithLatest, SnapshotOut
from app.security import require_session
from app.services import analytics

router = APIRouter(prefix="/api", tags=["media"], dependencies=[Depends(require_session)])


@router.get(
    "/accounts/{account_id}/media",
    response_model=MediaPage,
    summary="List collected posts with their newest metrics",
)
def list_media(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    order_by: Literal["timestamp", "engagement_rate", "collected_at"] = "timestamp",
    direction: Literal["asc", "desc"] = "desc",
    media_product_type: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
) -> MediaPage:
    base = analytics.media_query(
        account.id, media_product_type=media_product_type, search=search
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    stmt = analytics.apply_ordering(base, order_by, direction).limit(limit).offset(offset)
    rows = list(db.scalars(stmt).all())

    latest = analytics.get_latest_snapshots(db, [row.id for row in rows])
    items = []
    for row in rows:
        item = MediaWithLatest.model_validate(row)
        snapshot = latest.get(row.id)
        item.latest = SnapshotOut.model_validate(snapshot) if snapshot else None
        items.append(item)

    return MediaPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/media/{media_id}",
    response_model=MediaDetail,
    summary="One post with its full snapshot history",
)
def read_media(
    media_id: int,
    db: Session = Depends(get_db),
    history_limit: int = Query(default=200, ge=1, le=2000),
) -> MediaDetail:
    media = db.scalar(select(Media).where(Media.id == media_id))
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found.")

    # Years of history can be thousands of rows; take the newest `history_limit`
    # in the database rather than loading them all to throw most away.
    newest_first = db.scalars(
        select(MetricSnapshot)
        .where(MetricSnapshot.media_id == media.id)
        .order_by(MetricSnapshot.collected_at.desc(), MetricSnapshot.id.desc())
        .limit(history_limit)
    ).all()

    detail = MediaDetail.model_validate(media)
    detail.snapshots = [SnapshotOut.model_validate(row) for row in reversed(newest_first)]
    return detail
