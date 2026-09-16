"""Application settings, loaded from environment variables / the root .env file.

One settings object serves every process: the API, the Celery workers, Alembic
and the scripts. Sections mirror the two halves of the product — connected
accounts (Meta Graph API) and link analysis (scraping, NLP, AI insights) — plus
the shared infrastructure (PostgreSQL, Redis, Celery).
"""

from __future__ import annotations

import logging
import secrets
import stat
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# Where a generated development secret is persisted. Keeping it on disk is what
# stops every restart from silently invalidating the stored access tokens.
DEV_SECRET_FILE = BASE_DIR / "data" / ".secret_key"

# Built single-page app served by FastAPI (see frontend/).
DEFAULT_FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


class Settings(BaseSettings):
    """Central configuration object.

    Every value can be overridden through an environment variable of the same
    name (case-insensitive) or through the `.env` file at the project root.
    The file is located relative to this module, never the working directory,
    so `celery`, `alembic` and `uvicorn` read the same configuration wherever
    they are started from.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ app
    app_name: str = "Instagram Pipeline"
    app_version: str = __version__
    environment: Literal["development", "production", "test"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"

    # Public base URL of this app. Must match exactly what is registered as the
    # OAuth redirect URI in the Meta App dashboard.
    public_base_url: str = "http://localhost:8000"

    # Secret used to sign the OAuth `state` parameter and session cookies, and
    # to derive the token encryption key when `token_encryption_key` is unset.
    # Left empty it is NOT random: an empty value would silently derive a
    # publicly known Fernet key, so the validator below either generates and
    # persists one (development) or refuses to start (production).
    secret_key: str = ""

    # Password protecting the dashboard and the API. Empty disables the login
    # entirely, which is only accepted outside production.
    admin_password: str = ""
    session_max_age_hours: int = 12
    login_attempts_per_minute: int = Field(default=5, ge=1)

    # Fernet key used to encrypt access tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # When left empty a key is derived from `secret_key` so the app still runs
    # out of the box; set it explicitly in production.
    token_encryption_key: str = ""

    # Directory holding the built frontend (`npm run build`).
    frontend_dist_dir: Path = DEFAULT_FRONTEND_DIST

    # Origins allowed to call the API from another origin. The SPA is served by
    # the API itself, so this is only needed for unusual deployments.
    cors_origins: str = ""

    # --------------------------------------------------------------- database
    # Written with the synchronous driver name; the async engine used by the
    # link-analysis routes is derived from it (see `async_database_url`).
    database_url: str = "postgresql+psycopg://pipeline:pipeline@localhost:5432/instagram_pipeline"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 30_000
    # Run Alembic on startup. The container image migrates before starting the
    # server instead, so several API processes never race on the same DDL.
    auto_migrate: bool = True

    # ----------------------------------------------------------- redis/celery
    redis_url: str = "redis://localhost:6379/0"
    redis_pool_max: int = Field(default=20, ge=1)
    redis_timeout_seconds: float = 2.0
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    # celery = production. inline = run jobs inside the API process; only meant
    # for the automated tests, which have no broker.
    task_backend: Literal["celery", "inline"] = "celery"
    task_max_retries: int = 3
    # Scraping may take SCRAPER_BUDGET_SECONDS, then NLP and OpenAI run on top.
    task_soft_time_limit_seconds: int = 420
    task_time_limit_seconds: int = 480
    # A "processing" analysis untouched for this long is reported as failed.
    analysis_stale_after_seconds: int = 600
    cache_ttl_seconds: int = 86_400
    rate_limit_per_minute: int = Field(default=10, ge=1)
    # Only enable behind a trusted reverse proxy: X-Forwarded-For is spoofable.
    trust_proxy_headers: bool = False

    # ------------------------------------------------------------------ meta
    # "instagram_login" talks to graph.instagram.com and only needs an
    # Instagram Business/Creator account. "facebook_login" talks to
    # graph.facebook.com and additionally requires a linked Facebook Page.
    meta_login_flow: Literal["instagram_login", "facebook_login"] = "instagram_login"

    instagram_app_id: str = ""
    instagram_app_secret: str = ""

    # Graph API version used for every request.
    graph_api_version: str = "v23.0"

    # OAuth scopes requested during login.
    instagram_scopes: str = "instagram_business_basic,instagram_business_manage_insights"
    facebook_scopes: str = (
        "instagram_basic,instagram_manage_insights,"
        "pages_show_list,pages_read_engagement,business_management"
    )
    # Also request permission to read comments, so a link analysis of a post
    # from a connected account can take its comments from the official API
    # instead of scraping. Needs the permission approved in the Meta app.
    enable_graph_comments: bool = False

    # ------------------------------------------------------------- collection
    # Meta enforces a per-hour call budget. We stay well below it by batching.
    collection_interval_minutes: int = 360  # every 6 hours
    max_media_per_collection: int = 50
    api_calls_per_hour: int = 180
    # Back off when Meta reports this percentage of the app's call budget used.
    business_use_case_threshold: int = 80
    request_timeout_seconds: float = 20.0
    max_retries: int = 3
    # A collection run still `running` after this long belongs to a dead worker.
    collection_stale_after_minutes: int = 180

    # Refresh long-lived tokens once they are within this many days of expiry.
    token_refresh_threshold_days: int = 10
    token_refresh_interval_hours: int = 12

    # Periodic jobs (Celery beat).
    enable_scheduler: bool = True
    # How often tracked link analyses get a fresh metrics snapshot. 0 disables.
    tracking_interval_minutes: int = Field(default=720, ge=0)

    # --------------------------------------------------------------- scraping
    scraper_mode: Literal["auto", "apify", "instaloader", "mock"] = "auto"
    apify_token: SecretStr | None = None
    apify_base_url: str = "https://api.apify.com/v2"
    apify_post_actor: str = "apify~instagram-scraper"
    apify_comments_actor: str = "apify~instagram-comment-scraper"
    apify_timeout_seconds: int = 90
    apify_max_attempts: int = 3
    scraper_budget_seconds: int = 150
    max_comments: int = Field(default=500, ge=1, le=5000)
    instagram_username: str | None = None
    instagram_sessionfile: str | None = None

    # -------------------------------------------------------------------- nlp
    nlp_backend: Literal["transformers", "lexicon"] = "transformers"
    sentiment_model: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
    intent_model: str = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"
    nlp_intent_zero_shot: bool = True
    nlp_batch_size: int = Field(default=32, ge=1, le=512)
    nlp_device: Literal["auto", "cpu", "cuda"] = "auto"
    nlp_torch_threads: int | None = None
    # Load and warm the NLP models when a worker process starts. Only the
    # `analysis` worker needs them; the collection worker skips the cost.
    worker_preload_nlp: bool = False

    # ------------------------------------------------------------ ai insights
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4-turbo"
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2

    # ----------------------------------------------------------------- checks
    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("apify_token", "openai_api_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("instagram_username", "instagram_sessionfile", "nlp_torch_threads", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("token_encryption_key")
    @classmethod
    def _validate_fernet_key(cls, value: str) -> str:
        """Fail at startup, not at the first token write, on a malformed key."""
        key = value.strip()
        if not key:
            return key
        from cryptography.fernet import Fernet

        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
        return key

    @model_validator(mode="after")
    def _ensure_secret_key(self) -> Settings:
        """Guarantee a stable, non-guessable secret.

        An absent variable used to fall back to a fresh random key on every
        process start, which made every stored token unreadable after a
        restart. An empty variable — what copying `.env.example` verbatim
        produces — was worse: it derived the Fernet key from sha256("") , a
        value anybody can compute from this source file.
        """
        if self.secret_key.strip():
            return self
        if self.environment == "production":
            raise ValueError(
                "SECRET_KEY is empty and ENVIRONMENT=production. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
                "and put it in the .env file."
            )
        self.secret_key = _load_or_create_dev_secret()
        return self

    @model_validator(mode="after")
    def _require_login_in_production(self) -> Settings:
        """Without a password anyone reaching the URL can read the data, spend
        the Meta quota and run paid Apify/OpenAI analyses."""
        if self.environment == "production" and not self.admin_password:
            raise ValueError(
                "ADMIN_PASSWORD is empty and ENVIRONMENT=production. Set a password "
                "in the .env file: without it the dashboard and the API are public."
            )
        return self

    @model_validator(mode="after")
    def _ensure_sqlite_dir(self) -> Settings:
        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    # -------------------------------------------------------------- derived
    @property
    def is_configured(self) -> bool:
        """True when Meta credentials are present, i.e. OAuth can run."""
        return bool(self.instagram_app_id and self.instagram_app_secret)

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_base_url}/auth/callback"

    @property
    def graph_host(self) -> str:
        if self.meta_login_flow == "facebook_login":
            return "https://graph.facebook.com"
        return "https://graph.instagram.com"

    @property
    def scopes(self) -> str:
        if self.meta_login_flow == "facebook_login":
            base, extra = self.facebook_scopes, "instagram_manage_comments"
        else:
            base, extra = self.instagram_scopes, "instagram_business_manage_comments"
        if self.enable_graph_comments and extra not in base.split(","):
            return f"{base},{extra}"
        return base

    @property
    def auth_enabled(self) -> bool:
        """True when the dashboard and API require a session cookie."""
        return bool(self.admin_password)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sync_database_url(self) -> str:
        """URL for the blocking engine (routes, Celery, Alembic).

        Accepts an async-driver URL too, so an old `.env` written for the
        standalone analyzer (`postgresql+asyncpg://`) keeps working.
        """
        url = self.database_url
        for async_prefix, sync_prefix in _SYNC_PREFIXES.items():
            if url.startswith(async_prefix):
                return sync_prefix + url[len(async_prefix):]
        return url

    @property
    def async_database_url(self) -> str:
        """URL for the asyncio engine. psycopg 3 serves both worlds with one driver."""
        url = self.sync_database_url
        for sync_prefix, async_prefix in _ASYNC_PREFIXES.items():
            if url.startswith(sync_prefix):
                return async_prefix + url[len(sync_prefix):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


_SYNC_PREFIXES = {
    "postgresql+asyncpg://": "postgresql+psycopg://",
    "sqlite+aiosqlite://": "sqlite://",
}
_ASYNC_PREFIXES = {
    "postgresql+psycopg2://": "postgresql+psycopg://",
    "postgresql://": "postgresql+psycopg://",
    "sqlite://": "sqlite+aiosqlite://",
}


def _load_or_create_dev_secret() -> str:
    """Read the persisted development secret, creating it on first use."""
    try:
        existing = DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    generated = secrets.token_urlsafe(48)
    try:
        DEV_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEV_SECRET_FILE.write_text(generated, encoding="utf-8")
        DEV_SECRET_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
        logger.warning(
            "SECRET_KEY was not set; generated one and stored it at %s. "
            "Set SECRET_KEY in the .env file before deploying.",
            DEV_SECRET_FILE,
        )
    except OSError as exc:  # pragma: no cover - read-only filesystem
        logger.error(
            "SECRET_KEY is unset and %s could not be written (%s). Falling back "
            "to a per-process key: stored access tokens will not survive a "
            "restart. Set SECRET_KEY explicitly.",
            DEV_SECRET_FILE,
            exc,
        )
    return generated


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
