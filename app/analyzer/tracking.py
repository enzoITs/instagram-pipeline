"""Metric history for analysed links.

An analysis is a photograph: the numbers of a post at the moment it ran. The
connected-account side of this project exists because that photograph loses its
value quickly — Meta does not keep the history, so the app does. Tracking gives
a plain link the same treatment: every `TRACKING_INTERVAL_MINUTES` the metrics
(and only the metrics — no comments, no NLP, so no per-run cost worth worrying
about) are collected again into `video_metric_snapshots`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.models import EngagementMetrics, Video, VideoMetricSnapshot, VideoStatus
from app.analyzer.services.scraper_service import InstagramScraper, ScrapedPost, ScraperError
from app.config import Settings, get_settings
from app.database import session_scope
from app.engagement import compute_engagement_rate
from app.instagram.url import InvalidInstagramURLError, parse_instagram_url

logger = logging.getLogger(__name__)


def metrics_of(post: ScrapedPost) -> dict[str, int | None]:
    """The scraped/official counters under the names the shared formula expects."""
    return {
        "likes": post.likes,
        "comments": post.comments_count,
        "saved": post.saves,
        "shares": post.shares,
        "reach": post.reach,
        "views": post.views,
    }


def collect_current_metrics(
    db: Session, video: Video, *, settings: Settings | None = None
) -> ScrapedPost | None:
    """Fetch today's numbers for one video: official API first, scraper otherwise."""
    from app.analyzer import graph_source

    settings = settings or get_settings()
    try:
        parsed = parse_instagram_url(video.url)
    except InvalidInstagramURLError:  # pragma: no cover - stored URLs are canonical
        logger.warning("tracking_invalid_url", extra={"video_id": str(video.id)})
        return None

    owned = graph_source.load_owned_post(db, parsed, settings=settings)
    if owned is not None:
        return owned[0]
    try:
        with InstagramScraper(settings) as scraper:
            return scraper.scrape(video.url)
    except ScraperError as exc:
        logger.warning("tracking_scrape_failed", extra={"video_id": str(video.id), "error": str(exc)})
        return None


def record_snapshot(db: Session, video: Video, post: ScrapedPost, *, collected_at: datetime) -> VideoMetricSnapshot:
    """Append one point to this video's history and refresh its headline metrics."""
    metrics = metrics_of(post)
    rate, basis = compute_engagement_rate(metrics, post.followers_count)
    snapshot = VideoMetricSnapshot(
        video_id=video.id,
        collected_at=collected_at,
        source=post.source,
        views=post.views,
        likes=post.likes,
        comments_count=post.comments_count,
        shares=post.shares,
        saves=post.saves,
        reach=post.reach,
        engagement_rate=rate,
        engagement_basis=basis,
    )
    db.add(snapshot)

    current = db.scalars(select(EngagementMetrics).where(EngagementMetrics.video_id == video.id)).first()
    if current is not None:
        current.views, current.likes, current.comments_count = post.views, post.likes, post.comments_count
        current.shares, current.saves, current.reach = post.shares, post.saves, post.reach
        current.engagement_rate, current.engagement_basis = rate, basis
        current.collected_at = collected_at
    video.last_tracked_at = collected_at
    return snapshot


def refresh_tracked_videos(settings: Settings | None = None) -> int:
    """Take a fresh snapshot of every tracked analysis. Returns how many succeeded."""
    settings = settings or get_settings()
    with session_scope() as db:
        videos = list(
            db.scalars(
                select(Video).where(Video.tracked.is_(True), Video.status == VideoStatus.COMPLETED)
            )
        )
    refreshed = 0
    for video in videos:
        collected_at = datetime.now(timezone.utc)
        try:
            with session_scope() as db:
                stored = db.get(Video, video.id)
                if stored is None:  # deleted while the loop was running
                    continue
                post = collect_current_metrics(db, stored, settings=settings)
                if post is None:
                    continue
                record_snapshot(db, stored, post, collected_at=collected_at)
                refreshed += 1
        except Exception:  # noqa: BLE001 - one bad link must not stop the others
            logger.exception("tracking_refresh_failed", extra={"video_id": str(video.id)})
    if videos:
        logger.info("tracking_refreshed", extra={"tracked": len(videos), "refreshed": refreshed})
    return refreshed


def snapshot_rows(snapshots: list[VideoMetricSnapshot]) -> list[dict[str, Any]]:
    """History as plain dictionaries, oldest first (what the dashboard charts)."""
    return [
        {
            "collected_at": snapshot.collected_at,
            "views": snapshot.views,
            "likes": snapshot.likes,
            "comments": snapshot.comments_count,
            "shares": snapshot.shares,
            "saves": snapshot.saves,
            "reach": snapshot.reach,
            "engagement_rate": snapshot.engagement_rate,
            "engagement_basis": snapshot.engagement_basis,
        }
        for snapshot in sorted(snapshots, key=lambda row: row.collected_at)
    ]
