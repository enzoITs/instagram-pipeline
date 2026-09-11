"""Consistent error envelope: {"status": "error", "error": {"code", "message", ...}}."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_DEFAULT_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limit_exceeded",
    503: "service_unavailable",
}


class APIError(Exception):
    """Raise from endpoints to return a structured error with a stable `code`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_body(
    code: str,
    message: str,
    *,
    error_id: str | None = None,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if error_id:
        error["error_id"] = error_id
    if details:
        error["details"] = details
    return {"status": "error", "error": error}


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code, headers=exc.headers)


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _DEFAULT_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Erro na requisição"
    if exc.status_code == 404 and message == "Not Found":
        message = "Recurso não encontrado"
    return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)


async def validation_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Request validation failures are client errors: 400 (not FastAPI's default 422)."""
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    details = [
        {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg")), "type": error.get("type")}
        for error in errors
    ]
    first = details[0] if details else {"loc": [], "msg": "Requisição inválida"}
    message = str(first["msg"]).removeprefix("Value error, ")
    code = "invalid_url" if "url" in first["loc"] else "validation_error"
    return JSONResponse(error_body(code, message, details=details), status_code=400)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the stack trace under a unique id and return it to the client."""
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={"error_id": error_id, "path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        error_body(
            "internal_error",
            "Erro interno do servidor. Informe o error_id ao suporte.",
            error_id=error_id,
        ),
        status_code=500,
        headers={"X-Error-ID": error_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
