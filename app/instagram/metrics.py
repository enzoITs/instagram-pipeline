"""Metric definitions, per-media-type metric sets and derived calculations.

Meta deprecated ``impressions``, ``plays``, ``video_views`` and every
``carousel_album_*`` metric in Graph API v22.0 (21 April 2025) and replaced
them with the single ``views`` metric. This module only asks for metrics that
are still supported, and degrades gracefully when a specific media type does
not support one of them.
"""

from __future__ import annotations

from typing import Any, Iterable

# Metrics fetched from the /insights edge, grouped by `media_product_type`.
INSIGHT_METRICS: dict[str, tuple[str, ...]] = {
    "FEED": ("reach", "saved", "shares", "views", "total_interactions"),
    "REELS": ("reach", "saved", "shares", "views", "total_interactions"),
    "STORY": ("reach", "shares", "views", "total_interactions", "profile_visits", "follows"),
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

# Columns on MetricSnapshot that can be filled straight from an insight name.
SNAPSHOT_METRIC_COLUMNS: tuple[str, ...] = (
    "likes",
    "comments",
    "saved",
    "shares",
    "reach",
    "views",
    "total_interactions",
    "profile_visits",
    "follows",
)

# Interactions that make up the numerator of the engagement rate.
ENGAGEMENT_COMPONENTS: tuple[str, ...] = ("likes", "comments", "saved", "shares")


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


def compute_engagement_rate(
    metrics: dict[str, int | None],
    followers_count: int | None = None,
) -> tuple[float | None, str | None]:
    """Compute the engagement rate as a percentage.

    Returns ``(rate, basis)`` where *basis* is the denominator that was used —
    ``"reach"`` when reach is known (the meaningful denominator, since it counts
    the accounts that actually saw the post) or ``"followers"`` as a fallback.
    Returns ``(None, None)`` when neither denominator is usable, so the
    dashboard can show "no data" instead of a misleading zero.
    """
    interactions = _sum_present(metrics, ENGAGEMENT_COMPONENTS)
    if interactions is None:
        return None, None

    reach = metrics.get("reach")
    if isinstance(reach, int) and reach > 0:
        return round(interactions / reach * 100, 2), "reach"

    if isinstance(followers_count, int) and followers_count > 0:
        return round(interactions / followers_count * 100, 2), "followers"

    return None, None


def total_interactions(metrics: dict[str, int | None]) -> int | None:
    """Sum of the interaction metrics, preferring Meta's own total."""
    reported = metrics.get("total_interactions")
    if isinstance(reported, int):
        return reported
    return _sum_present(metrics, ENGAGEMENT_COMPONENTS)


def _sum_present(metrics: dict[str, int | None], keys: Iterable[str]) -> int | None:
    """Sum the given keys, ignoring missing ones. None when all are missing."""
    values = [metrics.get(key) for key in keys]
    present = [v for v in values if isinstance(v, int)]
    if not present:
        return None
    return sum(present)
