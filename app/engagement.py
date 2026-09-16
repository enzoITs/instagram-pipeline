"""The one engagement formula, shared by account collection and link analysis.

    rate = (likes + comments + saved + shares) / denominator * 100

The denominator is the best audience measurement available, in this order:

1. ``reach``     — unique accounts that saw the post (official API, own posts)
2. ``views``     — plays of a Reel (public data, any post)
3. ``followers`` — the account's audience, when the post itself has none

The basis is always returned next to the rate, so a screen never compares a
reach-based rate with a views-based one without saying so. Missing counters
are skipped rather than counted as zero, and "no denominator" yields ``None``
instead of a misleading ``0``.

Keep these functions pure: they are the definition users read in the README.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

EngagementBasis = Literal["reach", "views", "followers"]

# Interactions that make up the numerator of the engagement rate.
ENGAGEMENT_COMPONENTS: tuple[str, ...] = ("likes", "comments", "saved", "shares")


def sum_interactions(
    metrics: Mapping[str, int | None], keys: Iterable[str] = ENGAGEMENT_COMPONENTS
) -> int | None:
    """Sum the given counters, ignoring missing ones. None when all are missing."""
    present = [value for value in (metrics.get(key) for key in keys) if _is_count(value)]
    if not present:
        return None
    return sum(present)  # type: ignore[arg-type]


def compute_engagement_rate(
    metrics: Mapping[str, int | None],
    followers_count: int | None = None,
) -> tuple[float | None, EngagementBasis | None]:
    """Return ``(rate_percent, basis)``; ``(None, None)`` when it cannot be trusted.

    A reported ``reach == 0`` is a measurement, not a gap: the post reached
    nobody. Falling back to views or followers there would invent an audience,
    so zero reach with interactions on top of it — which Meta reports for posts
    published before the profile became a business account — has no rate.
    """
    interactions = sum_interactions(metrics)
    if interactions is None:
        return None, None

    reach = metrics.get("reach")
    if _is_count(reach):
        if reach > 0:  # type: ignore[operator]
            return _percent(interactions, reach), "reach"  # type: ignore[arg-type]
        return (0.0, "reach") if interactions == 0 else (None, None)

    views = metrics.get("views")
    if _is_count(views) and views > 0:  # type: ignore[operator]
        return _percent(interactions, views), "views"  # type: ignore[arg-type]

    if _is_count(followers_count) and followers_count > 0:  # type: ignore[operator]
        return _percent(interactions, followers_count), "followers"  # type: ignore[arg-type]

    return None, None


def calculate_engagement_score(engagement_rate: float | None, positive_ratio: float) -> float | None:
    """0–10 score: ``min(rate / 20 * 10 + positive_ratio * 3, 10)``.

    A 20 % rate alone maxes the score; the share of positive comments (among
    non-spam ones) adds up to 3 points on top of a weaker rate.
    """
    if engagement_rate is None:
        return None
    positive_ratio = max(0.0, min(1.0, positive_ratio))
    return round(max(0.0, min(engagement_rate / 20 * 10 + positive_ratio * 3, 10.0)), 2)


def _percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 2)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
