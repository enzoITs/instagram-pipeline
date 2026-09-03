"""Tests for metric parsing and the derived engagement-rate calculation."""

from __future__ import annotations

import pytest

from app.instagram.metrics import (
    compute_engagement_rate,
    metrics_for,
    parse_insights,
    total_interactions,
)


class TestMetricsFor:
    def test_reels_metrics_exclude_deprecated_names(self) -> None:
        metrics = metrics_for("REELS")
        assert "views" in metrics
        # Removed by Meta in Graph API v22.0.
        assert "impressions" not in metrics
        assert "plays" not in metrics
        assert "video_views" not in metrics

    def test_unknown_type_falls_back_to_the_safe_set(self) -> None:
        assert metrics_for(None) == ("reach", "saved", "shares", "views")
        assert metrics_for("SOMETHING_NEW") == ("reach", "saved", "shares", "views")

    def test_ads_have_no_insight_metrics(self) -> None:
        assert metrics_for("AD") == ()

    def test_lookup_is_case_insensitive(self) -> None:
        assert metrics_for("reels") == metrics_for("REELS")


class TestParseInsights:
    def test_flattens_the_api_envelope(self) -> None:
        payload = {
            "data": [
                {"name": "reach", "values": [{"value": 1200}]},
                {"name": "saved", "values": [{"value": 45}]},
            ]
        }
        assert parse_insights(payload) == {"reach": 1200, "saved": 45}

    def test_missing_values_are_omitted_not_zeroed(self) -> None:
        payload = {"data": [{"name": "views", "values": []}]}
        assert parse_insights(payload) == {}

    def test_breakdown_dicts_are_summed(self) -> None:
        payload = {
            "data": [{"name": "navigation", "values": [{"value": {"tap_forward": 5, "tap_back": 2}}]}]
        }
        assert parse_insights(payload) == {"navigation": 7}

    def test_empty_payload_is_safe(self) -> None:
        assert parse_insights({}) == {}
        assert parse_insights({"data": None}) == {}


class TestEngagementRate:
    def test_uses_reach_as_the_denominator(self) -> None:
        metrics = {"likes": 100, "comments": 20, "saved": 30, "shares": 10, "reach": 1000}
        assert compute_engagement_rate(metrics) == (16.0, "reach")

    def test_falls_back_to_followers_when_reach_is_zero(self) -> None:
        rate, basis = compute_engagement_rate({"likes": 100, "comments": 20, "reach": 0}, 5000)
        assert (rate, basis) == (2.4, "followers")

    def test_returns_none_when_no_denominator_is_available(self) -> None:
        assert compute_engagement_rate({"likes": 10}) == (None, None)

    def test_returns_none_when_no_interactions_were_collected(self) -> None:
        assert compute_engagement_rate({"reach": 100}) == (None, None)

    def test_partial_interactions_still_produce_a_rate(self) -> None:
        # `saved` and `shares` are missing; the rate uses what is available.
        rate, basis = compute_engagement_rate({"likes": 50, "comments": 5, "reach": 500})
        assert (rate, basis) == (11.0, "reach")

    @pytest.mark.parametrize("followers", [None, 0, -1])
    def test_invalid_follower_counts_are_ignored(self, followers) -> None:
        assert compute_engagement_rate({"likes": 5, "reach": 0}, followers) == (None, None)


class TestTotalInteractions:
    def test_prefers_metas_own_total(self) -> None:
        assert total_interactions({"total_interactions": 99, "likes": 5}) == 99

    def test_sums_components_when_meta_did_not_report_a_total(self) -> None:
        assert total_interactions({"likes": 5, "comments": 1, "saved": 2}) == 8

    def test_returns_none_when_nothing_is_known(self) -> None:
        assert total_interactions({"reach": 10}) is None
