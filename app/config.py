"""Application settings, loaded from environment variables / .env file."""

from __future__ import annotations

import logging
import secrets
import stat
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# Where a generated development secret is persisted. Keeping it on disk is what
# stops every restart from silently invalidating the stored access tokens.
DEV_SECRET_FILE = BASE_DIR / "data" / ".secret_key"


class Settings(BaseSettings):
    """Central configuration object.

    Every value can be overridden through an environment variable of the same
    name (case-insensitive) or through the `.env` file at the project root.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ app
    app_name: str = "Instagram Pipeline"
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

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
    # entirely, which is only appropriate on a machine nobody else can reach.
    admin_password: str = ""
    session_max_age_hours: int = 12

    # Fernet key used to encrypt access tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # When left empty a key is derived from `secret_key` so the app still runs
    # out of the box; set it explicitly in production.
    token_encryption_key: str = ""

    # --------------------------------------------------------------- database
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'instagram_pipeline.db'}"

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

    # ------------------------------------------------------------- collection
    # Meta enforces a per-hour call budget. We stay well below it by batching.
    collection_interval_minutes: int = 360  # every 6 hours
    max_media_per_collection: int = 50
    api_calls_per_hour: int = 180
    # Back off when Meta reports this percentage of the app's call budget used.
    business_use_case_threshold: int = 80
    request_timeout_seconds: float = 20.0
    max_retries: int = 3

    # Refresh long-lived tokens once they are within this many days of expiry.
    token_refresh_threshold_days: int = 10
    token_refresh_interval_hours: int = 12

    enable_scheduler: bool = True

    # ----------------------------------------------------------------- checks
    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

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
            return self.facebook_scopes
        return self.instagram_scopes


    @property
    def auth_enabled(self) -> bool:
        """True when the dashboard and API require a session cookie."""
        return bool(self.admin_password)


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
