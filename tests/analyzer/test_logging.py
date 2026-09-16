import json
import logging
import sys

import pytest

from app.config import Settings
from app.database import check_database, dispose_async_engine, session_scope
from app.logging_config import JsonFormatter, TextFormatter, configure_logging, redact, request_id_var


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer apify_api_ABCDEF123456789", "ABCDEF123456789"),
        ("calling with key sk-proj-abcdefghijklmnop1234", "abcdefghijklmnop1234"),
        ("token apify_api_ZXCVBNM12345678", "ZXCVBNM12345678"),
        ("GET /x?token=supersecret&page=1", "supersecret"),
        ('{"api_key": "abc123secret"}', "abc123secret"),
        ("postgresql+asyncpg://analyzer:p4ssw0rd@db:5432/app", "p4ssw0rd"),
        ("password=hunter2", "hunter2"),
        ("hf_abcdefghijklmnopqrstu", "abcdefghijklmnopqrstu"),
    ],
)
def test_redact_masks_secrets(raw, secret):
    assert secret not in redact(raw)


def test_redact_keeps_normal_text():
    assert redact("analysis_completed comments=257") == "analysis_completed comments=257"


def make_record(message: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 10, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_structure_and_redaction():
    token = request_id_var.set("req-1")
    try:
        output = JsonFormatter().format(make_record("apify call token=abc123secret", video_id="v1", duration_ms=12))
    finally:
        request_id_var.reset(token)
    payload = json.loads(output)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["module"] == "test_logging"
    assert payload["timestamp"].endswith("Z")
    assert payload["request_id"] == "req-1"
    assert payload["video_id"] == "v1" and payload["duration_ms"] == 12
    assert "abc123secret" not in output


def test_json_formatter_includes_stack_traces():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_text_formatter():
    token = request_id_var.set("req-2")
    try:
        output = TextFormatter().format(make_record("hello", step=3))
    finally:
        request_id_var.reset(token)
    assert "hello" in output and "step=3" in output and "request_id=req-2" in output


def test_configure_logging_is_idempotent():
    configure_logging(Settings(log_format="json", log_level="INFO"))
    configure_logging(Settings(log_format="text", log_level="WARNING"))
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, TextFormatter)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").propagate is False


async def test_check_database(db_engine, monkeypatch):
    assert await check_database() is True
    await dispose_async_engine()

    from app import database

    class BrokenEngine:
        def connect(self):
            raise OSError("connection refused")

    monkeypatch.setattr(database, "get_async_engine", lambda: BrokenEngine())
    assert await check_database(timeout=0.5) is False


def test_session_scope_rolls_back_on_error(db_engine):
    from app.models import Video

    with pytest.raises(RuntimeError):
        with session_scope() as session:
            session.add(Video(instagram_id="ROLLBACK1", url="https://www.instagram.com/p/ROLLBACK1/"))
            session.flush()
            raise RuntimeError("abort")
    with session_scope() as session:
        assert session.query(Video).count() == 0
