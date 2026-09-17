"""Metric definitions, per-media-type metric sets and derived calculations.

Meta deprecated ``impressions``, ``plays``, ``video_views`` and every
``carousel_album_*`` metric in Graph API v22.0 (21 April 2025) and replaced
them with the single ``views`` metric. This module only asks for metrics that
are still supported, and degrades gracefully when a specific media type does
not support one of them.
"""

from __future__ import annotations

from typing import Any

# The engagement formula lives in app.engagement so account collection and
# link analysis share one definition; re-exported here for existing callers.
from app.engagement import ENGAGEMENT_COMPONENTS, compute_engagement_rate, sum_interactions

__all__ = [
    "ACCOUNT_FIELDS",
    "DEFAULT_INSIGHT_METRICS",
    "ENGAGEMENT_COMPONENTS",
    "INSIGHT_METRICS",
    "MEDIA_FIELDS",
    "compute_engagement_rate",
    "metrics_for",
    "parse_insights",
    "total_interactions",
]

# Metrics fetched from the /insights edge, grouped by `media_product_type`.
# Stories are deliberately absent: the /media edge this pipeline reads never
# returns them, they expire after 24 hours, and collecting them would need a
# separate edge plus a much tighter schedule. See the README limitations.
INSIGHT_METRICS: dict[str, tuple[str, ...]] = {
    "FEED": ("reach", "saved", "shares", "views", "total_interactions"),
    "REELS": ("reach", "saved", "shares", "views", "total_interactions"),
    "AD": (),  # Ads are not supported by the media insights edge.
}

# Used when `media_product_type` is missing from the payload.
DEFAULT_INSIGHT_METRICS: tuple[str, ...] = ("reach", "saved", "shares", "views")

# Fields requested on the media node itself. `like_count` and `comments_count`
# come from here rather than from /insights, because they are available for
# every media type and never fail.
MEDIA_FIELDS: tuple[str, ...] = (
    "id",
    "caption",
    "media_type",
    "media_product_type",
    "media_url",
    "permalink",
    "thumbnail_url",
    "timestamp",
    "like_count",
    "comments_count",
)

ACCOUNT_FIELDS: tuple[str, ...] = (
    "id",
    "username",
    "name",
    "account_type",
    "profile_picture_url",
    "followers_count",
    "media_count",
)

def metrics_for(media_product_type: str | None) -> tuple[str, ...]:
    """Return the insight metrics supported for a given media product type."""
    if not media_product_type:
        return DEFAULT_INSIGHT_METRICS
    return INSIGHT_METRICS.get(media_product_type.upper(), DEFAULT_INSIGHT_METRICS)


def parse_insights(payload: dict[str, Any]) -> dict[str, int]:
    """Flatten an ``/insights`` response into ``{metric_name: value}``.

    The API returns ``{"data": [{"name": "reach", "values": [{"value": 12}]}]}``.
    Metrics without a usable value are omitted rather than stored as zero, so a
    missing metric is never confused with a real zero.
    """
    result: dict[str, int] = {}
    for entry in payload.get("data") or []:
        name = entry.get("name")
        if not name:
            continue
        values = entry.get("values") or []
        if not values:
            continue
        value = values[0].get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # `navigation` and friends return a breakdown dict; sum its numbers.
            if isinstance(value, dict):
                numeric = [v for v in value.values() if isinstance(v, (int, float))]
                if numeric:
                    result[name] = int(sum(numeric))
            continue
        result[name] = int(value)
    return result


def total_interactions(metrics: dict[str, int | None]) -> int | None:
    """Sum of the interaction metrics, preferring Meta's own total."""
    reported = metrics.get("total_interactions")
    if isinstance(reported, int):
        return reported
    return sum_interactions(metrics)
