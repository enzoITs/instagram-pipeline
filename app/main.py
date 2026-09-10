"""FastAPI application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import scheduler as scheduler_module
from app.config import get_settings
from app.crypto import TokenDecryptionError
from app.database import init_db
from app.instagram.client import GraphAPIError
from app.instagram.oauth import OAuthError
from app.routers import accounts, auth, jobs, media, system
from app.services.jobs_runtime import sweep_orphaned_runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Chart.js is the only third-party asset the dashboard loads. Everything else is
# served from this origin, so the policy can stay tight.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        # Inline style *attributes* are used by the dashboard markup.
        "style-src 'self' 'unsafe-inline'",
        # Post thumbnails are served from Meta's CDN, whose host varies.
        "img-src 'self' data: https:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)

# Swagger UI and ReDoc ship their own inline bootstrap script, which the policy
# above would block. They are developer tools, so they opt out instead.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    logger.info("Database ready at %s", settings.database_url)
    # A crash mid-collection leaves a run marked `running`, which would block
    # every later collection for that account.
    sweep_orphaned_runs()
    if not settings.is_configured:
        logger.warning(
            "INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET are not set. The dashboard "
            "runs, but connecting an account will fail. See SETUP.md."
        )
    if not settings.auth_enabled:
        logger.warning(
            "ADMIN_PASSWORD is not set: the dashboard and the API are open to "
            "anyone who can reach this address. Set it before exposing the app "
            "beyond localhost."
        )
    scheduler_module.start_scheduler()
    try:
        yield
    finally:
        scheduler_module.shutdown_scheduler()


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Automated collection and history of Instagram engagement metrics "
        "from the official Meta Graph API."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(media.router)
app.include_router(jobs.router)
app.include_router(system.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN201
    """Attach the baseline browser hardening headers to every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.path not in _CSP_EXEMPT_PATHS:
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if settings.public_base_url.startswith("https://"):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.exception_handler(GraphAPIError)
async def graph_api_error_handler(request: Request, exc: GraphAPIError) -> JSONResponse:
    """Turn Meta's errors into a readable 502 instead of a 500 stack trace."""
    logger.warning("Graph API error on %s: %s", request.url.path, exc)
    status_code = 401 if exc.is_token_error else 502
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": exc.message,
            "meta_error_code": exc.code,
            "meta_error_subcode": exc.subcode,
            "hint": (
                "Reconnect the account from the dashboard."
                if exc.is_token_error
                else "Check SETUP.md for permission and account-type requirements."
            ),
        },
    )


@app.exception_handler(OAuthError)
async def oauth_error_handler(request: Request, exc: OAuthError) -> JSONResponse:
    logger.warning("OAuth error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(TokenDecryptionError)
async def token_decryption_error_handler(
    request: Request, exc: TokenDecryptionError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the single-page dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")
