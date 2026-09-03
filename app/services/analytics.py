"""Read-only queries that back the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Account, Media, MetricSnapshot

# Metric columns that are summed or averaged across snapshots.
_SUMMABLE = ("likes", "comments", "saved", "shares", "reach", "views")


def latest_snapshot_subquery():
    """Subquery yielding the newest snapshot id for each media row."""
    return (
        select(
            MetricSnapshot.media_id.label("media_id"),
            func.max(MetricSnapshot.collected_at).label("max_collected_at"),
        )
        .group_by(MetricSnapshot.media_id)
        .subquery()
    )


def get_latest_snapshots(db: Session, media_ids: Sequence[int]) -> dict[int, MetricSnapshot]:
    """Return ``{media_id: newest snapshot}`` for the given media rows."""
    if not media_ids:
        return {}
    newest = latest_snapshot_subquery()
    rows = db.scalars(
        select(MetricSnapshot)
        .join(
            newest,
            (MetricSnapshot.media_id == newest.c.media_id)
            & (MetricSnapshot.collected_at == newest.c.max_collected_at),
        )
        .where(MetricSnapshot.media_id.in_(media_ids))
    ).all()
    # A media row can have two snapshots with identical timestamps only if the
    # unique index were dropped; keep the highest id to stay deterministic.
    latest: dict[int, MetricSnapshot] = {}
    for row in rows:
        current = latest.get(row.media_id)
        if current is None or row.id > current.id:
            latest[row.media_id] = row
    return latest


def account_summary(db: Session, account: Account) -> dict[str, Any]:
    """Aggregate the newest snapshot of every media into headline totals."""
    newest = latest_snapshot_subquery()
    stmt = (
        select(
            func.count(MetricSnapshot.id),
            *[func.sum(getattr(MetricSnapshot, name)) for name in _SUMMABLE],
            func.avg(MetricSnapshot.engagement_rate),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .join(
            newest,
            (MetricSnapshot.media_id == newest.c.media_id)
            & (MetricSnapshot.collected_at == newest.c.max_collected_at),
        )
        .where(Media.account_id == account.id)
    )
    row = db.execute(stmt).one()
    counted, likes, comments, saved, shares, reach, views, avg_rate = row

    tracked_media = db.scalar(
        select(func.count(Media.id)).where(Media.account_id == account.id)
    ) or 0
    snapshots = db.scalar(
        select(func.count(MetricSnapshot.id))
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id)
    ) or 0
    first_collected, last_collected = db.execute(
        select(func.min(MetricSnapshot.collected_at), func.max(MetricSnapshot.collected_at))
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id)
    ).one()

    return {
        "tracked_media": tracked_media,
        "snapshots": snapshots,
        "total_likes": int(likes or 0),
        "total_comments": int(comments or 0),
        "total_saved": int(saved or 0),
        "total_shares": int(shares or 0),
        "total_reach": int(reach or 0),
        "total_views": int(views or 0),
        "average_engagement_rate": round(float(avg_rate), 2) if avg_rate is not None else None,
        "first_collected_at": first_collected,
        "last_collected_at": last_collected,
        "_counted_media": counted,
    }


def engagement_timeseries(
    db: Session, account: Account, *, days: int = 90
) -> list[dict[str, Any]]:
    """One point per collection run: totals across all media collected then."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            MetricSnapshot.collected_at.label("collected_at"),
            func.count(MetricSnapshot.id).label("media_count"),
            *[
                func.sum(getattr(MetricSnapshot, name)).label(name)
                for name in _SUMMABLE
            ],
            func.avg(MetricSnapshot.engagement_rate).label("engagement_rate"),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id, MetricSnapshot.collected_at >= since)
        .group_by(MetricSnapshot.collected_at)
        .order_by(MetricSnapshot.collected_at)
    )
    points: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        mapping = row._mapping
        rate = mapping["engagement_rate"]
        points.append(
            {
                "collected_at": mapping["collected_at"],
                "media_count": int(mapping["media_count"] or 0),
                "engagement_rate": round(float(rate), 2) if rate is not None else None,
                **{name: int(mapping[name] or 0) for name in _SUMMABLE},
            }
        )
    return points


def media_type_breakdown(db: Session, account: Account) -> list[dict[str, Any]]:
    """Average engagement per media product type (FEED vs REELS vs STORY)."""
    newest = latest_snapshot_subquery()
    stmt = (
        select(
            func.coalesce(Media.media_product_type, "UNKNOWN").label("media_product_type"),
            func.count(MetricSnapshot.id).label("media_count"),
            func.avg(MetricSnapshot.engagement_rate).label("average_engagement_rate"),
            func.sum(MetricSnapshot.reach).label("total_reach"),
            func.sum(MetricSnapshot.views).label("total_views"),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .join(
            newest,
            (MetricSnapshot.media_id == newest.c.media_id)
            & (MetricSnapshot.collected_at == newest.c.max_collected_at),
        )
        .where(Media.account_id == account.id)
        .group_by(func.coalesce(Media.media_product_type, "UNKNOWN"))
        .order_by(func.count(MetricSnapshot.id).desc())
    )
    result: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        mapping = row._mapping
        rate = mapping["average_engagement_rate"]
        result.append(
            {
                "media_product_type": mapping["media_product_type"],
                "media_count": int(mapping["media_count"] or 0),
                "average_engagement_rate": round(float(rate), 2) if rate is not None else None,
                "total_reach": int(mapping["total_reach"] or 0),
                "total_views": int(mapping["total_views"] or 0),
            }
        )
    return result


def media_query(
    account_id: int,
    *,
    media_product_type: str | None = None,
    search: str | None = None,
) -> Select:
    """Base SELECT for the media list, with optional filters applied."""
    stmt = select(Media).where(Media.account_id == account_id)
    if media_product_type:
        stmt = stmt.where(Media.media_product_type == media_product_type.upper())
    if search:
        stmt = stmt.where(Media.caption.ilike(f"%{search}%"))
    return stmt


ORDERABLE_COLUMNS = {
    "timestamp": Media.timestamp,
    "engagement_rate": Media.latest_engagement_rate,
    "collected_at": Media.last_snapshot_at,
}


def apply_ordering(stmt: Select, order_by: str, direction: str) -> Select:
    """Order the media list by one of the whitelisted columns."""
    column = ORDERABLE_COLUMNS.get(order_by, Media.timestamp)
    ordering = column.asc() if direction == "asc" else column.desc()
    # NULLs last regardless of direction, then a stable tiebreaker.
    return stmt.order_by(column.is_(None), ordering, Media.id.desc())
