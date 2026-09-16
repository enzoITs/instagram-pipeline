"""Engagement formulas (kept pure so they are trivially testable)."""

from __future__ import annotations


def calculate_engagement_rate(
    views: int | None,
    likes: int | None,
    comments: int | None,
    shares: int | None,
    saves: int | None,
) -> float | None:
    """(likes + comments + shares*2 + saves*1.5) / views * 100.

    Returns None when views are unknown (e.g. image posts): a rate over an unknown
    audience would be meaningless. Missing counters contribute 0.
    """
    if not views or views <= 0:
        return None
    interactions = (likes or 0) + (comments or 0) + (shares or 0) * 2 + (saves or 0) * 1.5
    return round(interactions / views * 100, 2)


def calculate_engagement_score(engagement_rate: float | None, positive_ratio: float) -> float | None:
    """min(rate/20*10 + positive_ratio*3, 10), on a 0-10 scale."""
    if engagement_rate is None:
        return None
    positive_ratio = max(0.0, min(1.0, positive_ratio))
    return round(max(0.0, min(engagement_rate / 20 * 10 + positive_ratio * 3, 10.0)), 2)
