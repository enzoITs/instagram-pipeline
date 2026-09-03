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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    logger.info("Database ready at %s", settings.database_url)
    if not settings.is_configured:
        logger.warning(
            "INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET are not set. The dashboard "
            "runs, but connecting an account will fail. See SETUP.md."
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
