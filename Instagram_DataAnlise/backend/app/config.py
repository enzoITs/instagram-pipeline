"""Application settings loaded from environment variables (and optional .env files)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Root .env (shared with docker compose) first, backend/.env overrides it.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---------------------------------------------------------------
    app_name: str = "Instagram Engagement Analyzer"
    app_version: str = __version__
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # --- Database --------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://analyzer:analyzer@localhost:5432/analyzer"
    db_pool_min: int = Field(default=5, ge=1)
    db_pool_max: int = Field(default=50, ge=1)
    db_pool_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 30_000

    # --- Redis / Celery ----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_pool_max: int = Field(default=20, ge=1)
    redis_timeout_seconds: float = 2.0
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    task_backend: Literal["celery", "inline"] = "celery"
    task_max_retries: int = 3
    task_soft_time_limit_seconds: int = 270
    task_time_limit_seconds: int = 300
    # A "processing" analysis untouched for this long is reported as failed.
    analysis_stale_after_seconds: int = 600
    cache_ttl_seconds: int = 86_400

    # --- API ---------------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    rate_limit_per_minute: int = Field(default=10, ge=1)
    trust_proxy_headers: bool = False

    # --- Scraping ----------------------------------------------------------------
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

    # --- NLP ---------------------------------------------------------------------
    nlp_backend: Literal["transformers", "lexicon"] = "transformers"
    sentiment_model: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
    intent_model: str = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"
    nlp_intent_zero_shot: bool = True
    nlp_batch_size: int = Field(default=32, ge=1, le=512)
    nlp_device: Literal["auto", "cpu", "cuda"] = "auto"
    nlp_torch_threads: int | None = None

    # --- AI insights -------------------------------------------------------------
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4-turbo"
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 3

    @field_validator("apify_token", "openai_api_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("instagram_username", "instagram_sessionfile", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sync_database_url(self) -> str:
        """Blocking-driver URL used by the Celery worker and Alembic."""
        url = self.database_url
        replacements = {
            "postgresql+asyncpg://": "postgresql+psycopg2://",
            "sqlite+aiosqlite://": "sqlite://",
        }
        for async_prefix, sync_prefix in replacements.items():
            if url.startswith(async_prefix):
                return sync_prefix + url[len(async_prefix):]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
