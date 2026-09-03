"""Tests for the Graph API client: error handling, retries and rate limiting."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.instagram.client import GraphAPIError, InstagramClient, RateLimiter


class TestRateLimiter:
    def test_allows_calls_up_to_the_limit_without_waiting(self) -> None:
        waits: list[float] = []
        limiter = RateLimiter(3)
        for _ in range(3):
            limiter.acquire(sleeper=waits.append)
        assert waits == []
        assert limiter.calls_in_window == 3

    def test_waits_once_the_window_is_full(self) -> None:
        waits: list[float] = []
        limiter = RateLimiter(2, window_seconds=60)
        now = [0.0]

        def clock() -> float:
            return now[0]

        def sleeper(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds  # simulate time passing

        for _ in range(3):
            limiter.acquire(sleeper=sleeper, clock=clock)

        assert len(waits) == 1
        assert waits[0] == pytest.approx(60.0, abs=0.1)


class TestErrorParsing:
    @respx.mock
    def test_meta_error_envelope_becomes_a_graph_api_error(self, api_client) -> None:
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Invalid OAuth access token.",
                        "type": "OAuthException",
                        "code": 190,
                        "error_subcode": 463,
                        "fbtrace_id": "abc123",
                    }
                },
            )
        )
        with pytest.raises(GraphAPIError) as info:
            api_client.get("/me", access_token="bad")

        error = info.value
        assert error.code == 190
        assert error.subcode == 463
        assert error.is_token_error
        assert not error.is_rate_limited
        assert error.fbtrace_id == "abc123"

    @respx.mock
    def test_rate_limit_errors_are_flagged(self, api_client) -> None:
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(
                400, json={"error": {"message": "Application request limit reached", "code": 4}}
            )
        )
        with pytest.raises(GraphAPIError) as info:
            api_client.get("/me", access_token="token")
        assert info.value.is_rate_limited

    @respx.mock
    def test_unsupported_metric_is_detected_and_named(self, api_client) -> None:
        message = (
            "(#100) The following metrics are not supported for this media "
            "product type: impressions, plays"
        )
        respx.get("https://graph.instagram.com/v23.0/1/insights").mock(
            return_value=httpx.Response(400, json={"error": {"message": message, "code": 100}})
        )
        with pytest.raises(GraphAPIError) as info:
            api_client.get("/1/insights", access_token="token")

        error = info.value
        assert error.is_unsupported_metric
        assert error.unsupported_metrics(("reach", "impressions", "plays")) == {
            "impressions",
            "plays",
        }

    @respx.mock
    def test_non_json_response_raises_a_readable_error(self, api_client) -> None:
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
        )
        with pytest.raises(GraphAPIError, match="non-JSON"):
            api_client.get("/me", access_token="token")


class TestRetries:
    @respx.mock
    def test_transient_server_errors_are_retried(self, api_client) -> None:
        route = respx.get("https://graph.instagram.com/v23.0/me").mock(
            side_effect=[
                httpx.Response(500, json={"error": {"message": "boom", "code": 1}}),
                httpx.Response(200, json={"id": "42"}),
            ]
        )
        assert api_client.get("/me", access_token="token") == {"id": "42"}
        assert route.call_count == 2

    @respx.mock
    def test_token_errors_are_not_retried(self, api_client) -> None:
        route = respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(
                401, json={"error": {"message": "expired", "code": 190}}
            )
        )
        with pytest.raises(GraphAPIError):
            api_client.get("/me", access_token="token")
        assert route.call_count == 1

    @respx.mock
    def test_timeouts_are_retried_then_reported(self, api_client) -> None:
        route = respx.get("https://graph.instagram.com/v23.0/me").mock(
            side_effect=httpx.ConnectTimeout("too slow")
        )
        with pytest.raises(GraphAPIError, match="timed out"):
            api_client.get("/me", access_token="token")
        assert route.call_count == 2  # MAX_RETRIES=2 in the test environment


class TestPagination:
    @respx.mock
    def test_follows_next_links_up_to_the_limit(self, api_client) -> None:
        page_two = "https://graph.instagram.com/v23.0/1/media?after=cursor"
        respx.get("https://graph.instagram.com/v23.0/1/media").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": [{"id": "a"}, {"id": "b"}], "paging": {"next": page_two}},
                ),
                httpx.Response(200, json={"data": [{"id": "c"}]}),
            ]
        )
        items = api_client.get_paginated("/1/media", access_token="token")
        assert [item["id"] for item in items] == ["a", "b", "c"]

    @respx.mock
    def test_stops_early_once_the_limit_is_reached(self, api_client) -> None:
        respx.get("https://graph.instagram.com/v23.0/1/media").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                    "paging": {"next": "https://graph.instagram.com/next"},
                },
            )
        )
        items = api_client.get_paginated("/1/media", access_token="token", limit=2)
        assert [item["id"] for item in items] == ["a", "b"]


class TestUrlBuilding:
    def test_versioned_and_unversioned_paths(self, settings) -> None:
        client = InstagramClient(settings, sleeper=lambda _s: None)
        try:
            assert client._build_url("/me", base_url=None, versioned=True) == (
                "https://graph.instagram.com/v23.0/me"
            )
            assert client._build_url("/access_token", base_url=None, versioned=False) == (
                "https://graph.instagram.com/access_token"
            )
            absolute = "https://api.instagram.com/oauth/access_token"
            assert client._build_url(absolute, base_url=None, versioned=True) == absolute
        finally:
            client.close()


class TestUsageHeaders:
    @respx.mock
    def test_backs_off_when_meta_reports_a_nearly_exhausted_budget(self, settings) -> None:
        waits: list[float] = []
        client = InstagramClient(
            settings, rate_limiter=RateLimiter(10_000), sleeper=waits.append
        )
        try:
            respx.get("https://graph.instagram.com/v23.0/me").mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "1"},
                    headers={
                        "x-business-use-case-usage": (
                            '{"17841400000000001":[{"type":"instagram",'
                            '"call_count":95,"total_cputime":10,"total_time":12,'
                            '"estimated_time_to_regain_access":2}]}'
                        )
                    },
                )
            )
            client.get("/me", access_token="token")
        finally:
            client.close()

        assert waits == [120.0]  # 2 minutes, as Meta reported

    @respx.mock
    def test_no_backoff_below_the_threshold(self, settings) -> None:
        waits: list[float] = []
        client = InstagramClient(
            settings, rate_limiter=RateLimiter(10_000), sleeper=waits.append
        )
        try:
            respx.get("https://graph.instagram.com/v23.0/me").mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "1"},
                    headers={
                        "x-business-use-case-usage": (
                            '{"1":[{"call_count":10,"total_cputime":5,"total_time":5}]}'
                        )
                    },
                )
            )
            client.get("/me", access_token="token")
        finally:
            client.close()

        assert waits == []

    @respx.mock
    def test_a_malformed_usage_header_is_ignored(self, api_client) -> None:
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(
                200, json={"id": "1"}, headers={"x-business-use-case-usage": "not json"}
            )
        )
        assert api_client.get("/me", access_token="token") == {"id": "1"}
