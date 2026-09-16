"""Pure ASGI middlewares: request context/access log and Redis-backed rate limiting.

Pure ASGI (instead of BaseHTTPMiddleware) keeps contextvars intact and avoids
buffering responses.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid

from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.errors import error_body
from app.logging_config import request_id_var

logger = logging.getLogger("app.access")

_REQUEST_ID_REGEX = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_QUIET_PATHS = {"/health"}


def client_ip(scope: Scope, trust_proxy_headers: bool) -> str:
    """Resolve the client IP. Proxy headers are honoured only when explicitly trusted."""
    if trust_proxy_headers:
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        real_ip = headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded = [part.strip() for part in headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if forwarded:
            # Right-most entry is the one appended by our own proxy (not client-controlled).
            return forwarded[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestContextMiddleware:
    """Assigns X-Request-ID, measures latency and emits one structured access log per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next((value.decode("latin-1") for key, value in scope["headers"] if key == b"x-request-id"), "")
        request_id = incoming if _REQUEST_ID_REGEX.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                elapsed_ms = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                headers.append((b"x-process-time-ms", f"{elapsed_ms:.1f}".encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope.get("path", "")
            level = logging.DEBUG if path in _QUIET_PATHS and status_holder["status"] < 400 else logging.INFO
            logger.log(
                level,
                "request_completed",
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "status_code": status_holder["status"],
                    "duration_ms": duration_ms,
                    "client_ip": client_ip(scope, get_settings().trust_proxy_headers),
                },
            )
            request_id_var.reset(token)


class RateLimitMiddleware:
    """Sliding-window rate limit (per client IP) for analysis submissions.

    Uses the "sliding window counter" approximation: the previous fixed window's count is
    weighted by how much of it still overlaps the last 60 s. Counters live in Redis, so
    the limit holds across multiple API instances. If Redis is unreachable the limiter
    fails open (availability over strictness) and logs a warning.
    """

    window_seconds = 60

    def __init__(self, app: ASGIApp, limited_routes: set[tuple[str, str]] | None = None) -> None:
        self.app = app
        self.limited_routes = limited_routes or {("POST", "/api/v1/analyze")}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope.get("method"), scope.get("path")) not in self.limited_routes:
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        limit = settings.rate_limit_per_minute
        redis_client = getattr(scope["app"].state, "redis", None)
        if redis_client is None:
            await self.app(scope, receive, send)
            return

        ip = client_ip(scope, settings.trust_proxy_headers)
        now = time.time()
        current_window = int(now // self.window_seconds)
        elapsed = (now % self.window_seconds) / self.window_seconds
        current_key = f"ratelimit:analyze:{ip}:{current_window}"
        previous_key = f"ratelimit:analyze:{ip}:{current_window - 1}"
        try:
            pipe = redis_client.pipeline(transaction=True)
            pipe.incr(current_key)
            pipe.expire(current_key, self.window_seconds * 2)
            pipe.get(previous_key)
            current_count, _, previous_count = await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.warning("rate_limit_unavailable", extra={"error": type(exc).__name__})
            await self.app(scope, receive, send)
            return

        estimated = int(previous_count or 0) * (1 - elapsed) + int(current_count)
        remaining = max(0, math.floor(limit - estimated))
        rate_headers = [
            (b"x-ratelimit-limit", str(limit).encode()),
            (b"x-ratelimit-remaining", str(remaining).encode()),
        ]

        if estimated > limit:
            retry_after = max(1, math.ceil(self.window_seconds - (now % self.window_seconds)))
            logger.warning("rate_limit_exceeded", extra={"client_ip": ip, "limit": limit})
            body = json.dumps(
                error_body(
                    "rate_limit_exceeded",
                    f"Limite de {limit} análises por minuto excedido. Tente novamente em {retry_after}s.",
                ),
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(retry_after).encode()),
                        *rate_headers,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *rate_headers]
            await send(message)

        await self.app(scope, receive, send_with_headers)
