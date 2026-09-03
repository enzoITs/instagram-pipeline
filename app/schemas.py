"""Pydantic models describing the JSON the API returns."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ accounts
class AccountOut(ORMModel):
    id: int
    ig_user_id: str
    username: str | None = None
    name: str | None = None
    account_type: str | None = None
    profile_picture_url: str | None = None
    followers_count: int | None = None
    media_count: int | None = None
    is_active: bool
    last_collected_at: datetime | None = None
    token_expires_at: datetime | None = None
    created_at: datetime


class AccountSummary(BaseModel):
    """Headline numbers shown at the top of the dashboard."""

    account: AccountOut
    tracked_media: int
    snapshots: int
    total_likes: int
    total_comments: int
    total_saved: int
    total_shares: int
    total_reach: int
    total_views: int
    average_engagement_rate: float | None = None
    first_collected_at: datetime | None = None
    last_collected_at: datetime | None = None


# --------------------------------------------------------------------- media
class SnapshotOut(ORMModel):
    id: int
    collected_at: datetime
    likes: int | None = None
    comments: int | None = None
    saved: int | None = None
    shares: int | None = None
    reach: int | None = None
    views: int | None = None
    total_interactions: int | None = None
    profile_visits: int | None = None
    follows: int | None = None
    engagement_rate: float | None = None
    engagement_basis: str | None = None


class MediaOut(ORMModel):
    id: int
    ig_media_id: str
    media_type: str | None = None
    media_product_type: str | None = None
    caption: str | None = None
    permalink: str | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None
    timestamp: datetime | None = None
    last_snapshot_at: datetime | None = None
    latest_engagement_rate: float | None = None


class MediaWithLatest(MediaOut):
    latest: SnapshotOut | None = None


class MediaDetail(MediaOut):
    snapshots: list[SnapshotOut] = Field(default_factory=list)


class MediaPage(BaseModel):
    items: list[MediaWithLatest]
    total: int
    limit: int
    offset: int


# ----------------------------------------------------------------- analytics
class TimeseriesPoint(BaseModel):
    collected_at: datetime
    likes: int = 0
    comments: int = 0
    saved: int = 0
    shares: int = 0
    reach: int = 0
    views: int = 0
    engagement_rate: float | None = None
    media_count: int = 0


class MediaTypeBreakdown(BaseModel):
    media_product_type: str
    media_count: int
    average_engagement_rate: float | None = None
    total_reach: int = 0
    total_views: int = 0


# ----------------------------------------------------------------------- jobs
class CollectionRunOut(ORMModel):
    id: int
    account_id: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    trigger: str
    media_seen: int
    media_created: int
    snapshots_created: int
    api_calls: int
    error: str | None = None


class CollectionResultOut(BaseModel):
    run_id: int
    account_id: int
    status: str
    media_seen: int
    media_created: int
    snapshots_created: int
    api_calls: int
    error: str | None = None


class TokenRefreshOut(BaseModel):
    account_id: int
    username: str | None = None
    refreshed: bool
    expires_at: datetime | None = None
    error: str | None = None


# --------------------------------------------------------------------- system
class HealthOut(BaseModel):
    status: str
    environment: str
    database: str
    meta_configured: bool
    login_flow: str
    graph_api_version: str
    scheduler_running: bool
    connected_accounts: int
    next_collection_at: datetime | None = None


class MessageOut(BaseModel):
    detail: str
