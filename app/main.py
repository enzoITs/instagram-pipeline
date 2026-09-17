"""FastAPI application entry point: one API and one dashboard for both halves.

* `/auth/*`, `/api/accounts/*`, `/api/media/*`, `/api/runs/*` — connected
  accounts, their metric history and the collection jobs;
* `/api/v1/*` — analysis of any public Reel/Post by link;
* `/` — the built React single-page app that drives both.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.analyzer.services.cache_service import CacheService, create_async_redis
from app.config import get_settings
from app.crypto import TokenDecryptionError
from app.database import dispose_async_engine, init_db
from app.errors import error_body, register_exception_handlers
from app.instagram.client import GraphAPIError
from app.instagram.oauth import OAuthError
from app.logging_config import configure_logging
from app.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.routers import accounts, analysis, auth, jobs, media, system

logger = logging.getLogger(__name__)

settings = get_settings()

FRONTEND_DIR = Path(settings.frontend_dist_dir)
INDEX_FILE = FRONTEND_DIR / "index.html"

# The dashboard is served from this origin and loads no third-party script, so
# the policy can stay tight. Recharts and the theme use inline style attributes.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        # Post thumbnails are served from Meta's CDN, whose host varies.
        "img-src 'self' data: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)

# Swagger UI and ReDoc ship their own inline bootstrap script, which the policy
# above would block. They are developer tools, so they opt out instead.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})

API_DESCRIPTION = """
Duas metades do mesmo produto:

* **Contas conectadas** — coleta agendada das métricas oficiais (Graph API da Meta),
  histórico permanente por post e exportação.
* **Análise por link** — qualquer Reel/Post público: métricas, sentimento e intenção
  dos comentários (NLP), tópicos e 3–5 insights.

Quando o link é de um post de uma conta conectada, a análise usa as métricas oficiais
(alcance, salvamentos, compartilhamentos) em vez das públicas.

**Erros** seguem o envelope `{"status": "error", "error": {"code", "message", "error_id?"}}`.
"""

TAGS = [
    {"name": "auth", "description": "Login do painel e conexão de contas via OAuth."},
    {"name": "accounts", "description": "Contas conectadas e seus números consolidados."},
    {"name": "media", "description": "Posts coletados e histórico de métricas."},
    {"name": "jobs", "description": "Coletas manuais, renovação de token e execuções."},
    {"name": "analysis", "description": "Análise de engajamento por link."},
    {"name": "system", "description": "Saúde do serviço e exportação."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings)
    if settings.auto_migrate:
        init_db()
        logger.info("database_ready", extra={"url": settings.sync_database_url.split("@")[-1]})
    app.state.redis = create_async_redis(settings)
    app.state.cache = CacheService(app.state.redis, settings.cache_ttl_seconds)

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
    if not INDEX_FILE.exists():
        logger.warning(
            "The dashboard bundle is missing at %s. Build it with `npm --prefix frontend run build` "
            "or run the Vite dev server.",
            FRONTEND_DIR,
        )
    logger.info("api_started", extra={"version": __version__, "environment": settings.environment})
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await dispose_async_engine()
        logger.info("api_stopped")


app = FastAPI(
    title=settings.app_name,
    description=API_DESCRIPTION,
    version=__version__,
    openapi_tags=TAGS,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(media.router)
app.include_router(jobs.router)
app.include_router(system.router)
app.include_router(analysis.router)

# Order matters: the last middleware added is the outermost one.
app.add_middleware(RateLimitMiddleware)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Error-ID"],
    )
app.add_middleware(RequestContextMiddleware)


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
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ------------------------------------------------------------------- handlers
@app.exception_handler(GraphAPIError)
async def graph_api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn Meta's errors into a readable 502 instead of a 500 stack trace."""
    assert isinstance(exc, GraphAPIError)
    logger.warning("graph_api_error", extra={"path": request.url.path, "error": str(exc)})
    status_code = 401 if exc.is_token_error else 502
    hint = (
        "Reconnect the account from the dashboard."
        if exc.is_token_error
        else "Check SETUP.md for permission and account-type requirements."
    )
    body = error_body("meta_error", exc.message)
    body["error"].update({"meta_error_code": exc.code, "meta_error_subcode": exc.subcode, "hint": hint})
    body["detail"] = exc.message
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(OAuthError)
async def oauth_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("oauth_error", extra={"path": request.url.path, "error": str(exc)})
    body = error_body("oauth_error", str(exc))
    body["detail"] = str(exc)
    return JSONResponse(status_code=400, content=body)


@app.exception_handler(TokenDecryptionError)
async def token_decryption_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    body = error_body("token_unreadable", str(exc))
    body["detail"] = str(exc)
    return JSONResponse(status_code=409, content=body)


# ------------------------------------------------------------------ dashboard
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR / "assets", check_dir=False),
    name="assets",
)

_MISSING_BUNDLE_PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Instagram Pipeline</title></head>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto; line-height: 1.6">
<h1>Painel ainda não foi compilado</h1>
<p>A API está no ar (veja <a href="/docs">/docs</a>), mas o pacote do painel não foi encontrado.</p>
<pre>npm --prefix frontend ci
npm --prefix frontend run build</pre>
<p>Durante o desenvolvimento, use <code>npm --prefix frontend run dev</code> e acesse
http://localhost:5173.</p>
</body></html>
"""


@app.get("/", include_in_schema=False)
def dashboard() -> Any:
    """Serve the single-page dashboard."""
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_MISSING_BUNDLE_PAGE, status_code=200)


@app.get("/favicon.svg", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Any:
    icon = FRONTEND_DIR / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content=error_body("not_found", "Recurso não encontrado"))


# --------------------------------------------------------------------- OpenAPI
def custom_openapi() -> dict[str, Any]:
    """Document request-validation errors as 400 (what the API returns), not FastAPI's 422."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            responses = operation.get("responses", {})
            if responses.pop("422", None) is not None and "400" not in responses:
                responses["400"] = {
                    "description": "Requisição inválida",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                }
    for unused in ("HTTPValidationError", "ValidationError"):
        schema.get("components", {}).get("schemas", {}).pop(unused, None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
