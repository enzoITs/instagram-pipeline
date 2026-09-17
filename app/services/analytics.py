"""Read-only queries that back the dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models import Account, Media, MetricSnapshot

# Metric columns that are summed or averaged across snapshots.
_SUMMABLE = ("likes", "comments", "saved", "shares", "reach", "views")

# Interactions that make up the numerator of the engagement rate.
_INTERACTIONS = ("likes", "comments", "saved", "shares")


def _denominator_sum() -> Any:
    """Audience of a group of snapshots.

    Rows collected before `engagement_denominator` existed keep their reach or
    view count, so history written by an older version still averages correctly.
    """
    fallback = case(
        (MetricSnapshot.engagement_basis == "reach", MetricSnapshot.reach),
        (MetricSnapshot.engagement_basis == "views", MetricSnapshot.views),
        else_=None,
    )
    return func.sum(func.coalesce(MetricSnapshot.engagement_denominator, fallback))


def _interactions_sum() -> Any:
    """SQL sum of the four interaction counters, treating NULL as 0."""
    total: Any = func.coalesce(getattr(MetricSnapshot, _INTERACTIONS[0]), 0)
    for name in _INTERACTIONS[1:]:
        total = total + func.coalesce(getattr(MetricSnapshot, name), 0)
    return func.sum(total)


def weighted_rate(rows: Sequence[Sequence[Any]]) -> tuple[float | None, str | None]:
    """Engagement rate of a group of posts: sum of interactions over sum of audience.

    Averaging each post's percentage would give a post seen by 50 people the
    same weight as one seen by 50 000, and would silently mix rates measured
    against reach with rates measured against followers. Rows are
    ``(basis, interactions, audience, snapshots)``; the basis covering the most
    posts wins and is reported next to the number.
    """
    best: tuple[str, float, float, int] | None = None
    for basis, interactions, audience, count in rows:
        if not basis or not audience or float(audience) <= 0 or interactions is None:
            continue
        if best is None or int(count or 0) > best[3]:
            best = (str(basis), float(interactions), float(audience), int(count or 0))
    if best is None:
        return None, None
    return round(best[1] / best[2] * 100, 2), best[0]


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
    counted, likes, comments, saved, shares, reach, views = row

    rate_rows = db.execute(
        select(
            MetricSnapshot.engagement_basis,
            _interactions_sum(),
            _denominator_sum(),
            func.count(MetricSnapshot.id),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .join(
            newest,
            (MetricSnapshot.media_id == newest.c.media_id)
            & (MetricSnapshot.collected_at == newest.c.max_collected_at),
        )
        .where(Media.account_id == account.id)
        .group_by(MetricSnapshot.engagement_basis)
    ).all()
    avg_rate, basis = weighted_rate(rate_rows)

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
        "average_engagement_rate": avg_rate,
        "engagement_basis": basis,
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
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id, MetricSnapshot.collected_at >= since)
        .group_by(MetricSnapshot.collected_at)
        .order_by(MetricSnapshot.collected_at)
    )
    rates: dict[Any, list[tuple[Any, ...]]] = {}
    for basis_row in db.execute(
        select(
            MetricSnapshot.collected_at,
            MetricSnapshot.engagement_basis,
            _interactions_sum(),
            _denominator_sum(),
            func.count(MetricSnapshot.id),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .where(Media.account_id == account.id, MetricSnapshot.collected_at >= since)
        .group_by(MetricSnapshot.collected_at, MetricSnapshot.engagement_basis)
    ):
        rates.setdefault(basis_row[0], []).append(tuple(basis_row[1:]))

    points: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        mapping = row._mapping
        rate, basis = weighted_rate(rates.get(mapping["collected_at"], []))
        points.append(
            {
                "collected_at": mapping["collected_at"],
                "media_count": int(mapping["media_count"] or 0),
                "engagement_rate": rate,
                "engagement_basis": basis,
                **{name: int(mapping[name] or 0) for name in _SUMMABLE},
            }
        )
    return points


def media_type_breakdown(db: Session, account: Account) -> list[dict[str, Any]]:
    """Average engagement per media product type (FEED vs REELS)."""
    newest = latest_snapshot_subquery()
    product_type = func.coalesce(Media.media_product_type, "UNKNOWN")
    stmt = (
        select(
            product_type.label("media_product_type"),
            func.count(MetricSnapshot.id).label("media_count"),
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
        .group_by(product_type)
        .order_by(func.count(MetricSnapshot.id).desc())
    )

    rates: dict[str, list[tuple[Any, ...]]] = {}
    for row in db.execute(
        select(
            product_type,
            MetricSnapshot.engagement_basis,
            _interactions_sum(),
            _denominator_sum(),
            func.count(MetricSnapshot.id),
        )
        .select_from(MetricSnapshot)
        .join(Media, Media.id == MetricSnapshot.media_id)
        .join(
            newest,
            (MetricSnapshot.media_id == newest.c.media_id)
            & (MetricSnapshot.collected_at == newest.c.max_collected_at),
        )
        .where(Media.account_id == account.id)
        .group_by(product_type, MetricSnapshot.engagement_basis)
    ):
        rates.setdefault(str(row[0]), []).append(tuple(row[1:]))

    result: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        mapping = row._mapping
        rate, basis = weighted_rate(rates.get(str(mapping["media_product_type"]), []))
        result.append(
            {
                "media_product_type": mapping["media_product_type"],
                "media_count": int(mapping["media_count"] or 0),
                "average_engagement_rate": rate,
                "engagement_basis": basis,
                "total_reach": int(mapping["total_reach"] or 0),
                "total_views": int(mapping["total_views"] or 0),
            }
        )
    return result


def escape_like(term: str) -> str:
    """Neutralise LIKE wildcards so a search for "%" matches a literal percent.

    Without this, typing `%` or `_` in the dashboard search box silently
    matches every post instead of none.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        stmt = stmt.where(Media.caption.ilike(f"%{escape_like(search)}%", escape="\\"))
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
