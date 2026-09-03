"""HTTP client for the Instagram Graph API.

Handles the parts that make a naive `requests.get` unreliable in production:
Meta's error envelope, the per-hour call budget, transient 5xx responses and
the `X-Business-Use-Case-Usage` header that tells us how much of the budget the
app has already spent.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Meta error codes that mean "you are calling too often".
RATE_LIMIT_CODES = {4, 17, 32, 613, 80004}
# Meta error codes that mean "this access token is no longer usable".
TOKEN_ERROR_CODES = {102, 190}
TOKEN_ERROR_SUBCODES = {458, 460, 463, 467, 492}

_UNSUPPORTED_METRIC_RE = re.compile(
    r"metric\[\d+\]|\(#100\).*metric|does not support the metric|"
    r"metric.*(is not supported|not available)",
    re.IGNORECASE,
)


class GraphAPIError(RuntimeError):
    """A structured error returned by the Graph API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        error_type: str | None = None,
        fbtrace_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.fbtrace_id = fbtrace_id
        self.payload = payload or {}

    @property
    def is_rate_limited(self) -> bool:
        return self.status_code == 429 or (self.code in RATE_LIMIT_CODES)

    @property
    def is_token_error(self) -> bool:
        return self.code in TOKEN_ERROR_CODES or self.subcode in TOKEN_ERROR_SUBCODES

    @property
    def is_unsupported_metric(self) -> bool:
        if self.code != 100:
            return False
        return bool(_UNSUPPORTED_METRIC_RE.search(self.message))

    def unsupported_metrics(self, candidates: tuple[str, ...]) -> set[str]:
        """Metric names from *candidates* that appear in the error message."""
        lowered = self.message.lower()
        return {metric for metric in candidates if metric.lower() in lowered}

    def __str__(self) -> str:  # pragma: no cover - formatting only
        parts = [self.message]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.subcode is not None:
            parts.append(f"subcode={self.subcode}")
        if self.fbtrace_id:
            parts.append(f"fbtrace_id={self.fbtrace_id}")
        return " | ".join(parts)


class RateLimiter:
    """Thread-safe sliding-window limiter for calls per hour."""

    def __init__(self, max_calls_per_hour: int, window_seconds: int = 3600) -> None:
        self.max_calls = max(1, max_calls_per_hour)
        self.window = window_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, *, sleeper=time.sleep, clock=time.monotonic) -> None:
        """Block until another call fits inside the window."""
        while True:
            with self._lock:
                now = clock()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait_for = self.window - (now - self._calls[0]) + 0.01
            logger.warning(
                "Local rate limit reached (%s calls/hour); waiting %.1fs.",
                self.max_calls,
                wait_for,
            )
            sleeper(wait_for)

    @property
    def calls_in_window(self) -> int:
        with self._lock:
            return len(self._calls)


class InstagramClient:
    """Thin, retrying wrapper around the Graph API.

    The client is stateless with respect to accounts: the access token is passed
    per request, so one client can serve several connected accounts.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "instagram-pipeline/1.0"},
        )
        self.rate_limiter = rate_limiter or RateLimiter(self.settings.api_calls_per_hour)
        self._sleep = sleeper
        self.call_count = 0

    # ------------------------------------------------------------------ setup
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "InstagramClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------- public API
    def get(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, Any] | None = None,
        base_url: str | None = None,
        versioned: bool = True,
    ) -> dict[str, Any]:
        """Perform a GET request and return the decoded JSON body."""
        url = self._build_url(path, base_url=base_url, versioned=versioned)
        query: dict[str, Any] = {**(params or {}), "access_token": access_token}
        return self._request_with_retries("GET", url, params=query)

    def post(
        self,
        path: str,
        *,
        data: dict[str, Any],
        base_url: str | None = None,
        versioned: bool = True,
    ) -> dict[str, Any]:
        """Perform a POST request and return the decoded JSON body."""
        url = self._build_url(path, base_url=base_url, versioned=versioned)
        return self._request_with_retries("POST", url, data=data)

    def get_paginated(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Follow `paging.next` links, returning at most *limit* items."""
        items: list[dict[str, Any]] = []
        payload = self.get(path, access_token=access_token, params=params)

        while True:
            items.extend(payload.get("data") or [])
            if limit is not None and len(items) >= limit:
                return items[:limit]
            next_url = ((payload.get("paging") or {}).get("next")) or None
            if not next_url:
                return items
            # `next` is already a fully-qualified, token-bearing URL.
            payload = self._request_with_retries("GET", next_url)

    # -------------------------------------------------------------- internals
    def _build_url(self, path: str, *, base_url: str | None, versioned: bool) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        host = (base_url or self.settings.graph_host).rstrip("/")
        prefix = f"/{self.settings.graph_api_version}" if versioned else ""
        return f"{host}{prefix}/{path.lstrip('/')}"

    def _request_with_retries(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        last_error: GraphAPIError | None = None

        for attempt in range(self.settings.max_retries):
            self.rate_limiter.acquire(sleeper=self._sleep)
            self.call_count += 1
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                last_error = GraphAPIError(f"Request to Meta timed out: {exc}")
            except httpx.HTTPError as exc:
                last_error = GraphAPIError(f"Network error talking to Meta: {exc}")
            else:
                self._honour_usage_headers(response)
                try:
                    return self._parse_response(response)
                except GraphAPIError as exc:
                    last_error = exc
                    # A bad token or an unsupported metric will not fix itself.
                    if exc.is_token_error or exc.is_unsupported_metric:
                        raise
                    retryable = exc.is_rate_limited or (
                        exc.status_code is not None and exc.status_code >= 500
                    )
                    if not retryable:
                        raise

            if attempt < self.settings.max_retries - 1:
                backoff = min(60.0, 2.0 ** attempt * 2.0)
                logger.warning(
                    "Meta request failed (%s); retrying in %.0fs [attempt %s/%s].",
                    last_error,
                    backoff,
                    attempt + 1,
                    self.settings.max_retries,
                )
                self._sleep(backoff)

        raise last_error or GraphAPIError("Request to Meta failed for an unknown reason.")

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise GraphAPIError(
                f"Meta returned a non-JSON response (HTTP {response.status_code}): "
                f"{response.text[:200]}",
                status_code=response.status_code,
            ) from None

        if not isinstance(payload, dict):
            raise GraphAPIError(
                "Meta returned an unexpected payload shape.",
                status_code=response.status_code,
            )

        error = payload.get("error")
        if error or response.status_code >= 400:
            if not isinstance(error, dict):
                # Some OAuth endpoints use a flat error shape.
                error = {
                    "message": payload.get("error_message")
                    or payload.get("error_description")
                    or str(payload.get("error") or payload),
                    "type": payload.get("error_type"),
                }
            raise GraphAPIError(
                str(error.get("message") or "Unknown Graph API error"),
                status_code=response.status_code,
                code=_as_int(error.get("code")),
                subcode=_as_int(error.get("error_subcode")),
                error_type=error.get("type"),
                fbtrace_id=error.get("fbtrace_id"),
                payload=payload,
            )

        return payload

    def _honour_usage_headers(self, response: httpx.Response) -> None:
        """Slow down when Meta reports the call budget is nearly exhausted."""
        raw = response.headers.get("x-business-use-case-usage") or response.headers.get(
            "x-app-usage"
        )
        if not raw:
            return
        try:
            usage = json.loads(raw)
        except ValueError:
            return

        entries: list[dict[str, Any]] = []
        if isinstance(usage, dict):
            for value in usage.values():
                if isinstance(value, list):
                    entries.extend(v for v in value if isinstance(v, dict))
                elif isinstance(value, (int, float)):
                    entries.append(usage)
                    break

        threshold = self.settings.business_use_case_threshold
        for entry in entries:
            percentages = [
                _as_int(entry.get(key)) or 0
                for key in ("call_count", "total_cputime", "total_time")
            ]
            if max(percentages, default=0) < threshold:
                continue
            wait_minutes = _as_int(entry.get("estimated_time_to_regain_access")) or 1
            wait_seconds = min(wait_minutes * 60, 300)
            logger.warning(
                "Meta reports %s%% of the call budget used; pausing %ss.",
                max(percentages),
                wait_seconds,
            )
            self._sleep(wait_seconds)
            return


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
