"""Application settings, loaded from environment variables / .env file."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


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

    # Secret used to sign the OAuth `state` parameter and session cookies.
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))

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

    @model_validator(mode="after")
    def _ensure_sqlite_dir(self) -> "Settings":
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
