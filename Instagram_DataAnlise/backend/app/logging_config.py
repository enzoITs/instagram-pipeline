"""Structured logging with request correlation and secret redaction.

Every record carries timestamp, level, logger, module and message, plus any `extra`
fields and the current request id. Output is JSON by default (LOG_FORMAT=json) so it
can be shipped to any log aggregator; `text` is friendlier for local development.
Secrets (API tokens, bearer headers, database passwords) are masked before emission.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{6,}"), "Bearer ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), "sk-***"),
    (re.compile(r"\bapify_api_[A-Za-z0-9]{6,}"), "apify_api_***"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}"), "hf_***"),
    (
        re.compile(r"(?i)\b(token|api[_-]?key|apikey|password|passwd|secret)(\"?\s*[=:]\s*\"?)[^\s&\"',;}]+"),
        r"\1\2***",
    ),
    (re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
)

_RESERVED_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI duplicate of the message
}
_NOISY_LOGGERS = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "asyncio": logging.WARNING,
    "transformers": logging.WARNING,
    "huggingface_hub": logging.WARNING,
    "filelock": logging.WARNING,
    "instaloader": logging.WARNING,
    "openai": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
}


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = _extras(record)
        request_id = request_id_var.get()
        if request_id:
            fields["request_id"] = request_id
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        return redact(f"{base} {suffix}".rstrip())


def configure_logging(settings: Settings | None = None) -> None:
    """Idempotently route all loggers (app, uvicorn, celery) through one handler."""
    settings = settings or get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)
    # Uvicorn installs its own handlers; let records propagate to ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False  # replaced by RequestContextMiddleware's structured access log
