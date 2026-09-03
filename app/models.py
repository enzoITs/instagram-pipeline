"""ORM models: accounts, media, metric snapshots and collection runs."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, UTCDateTime


def utcnow() -> datetime:
    """Timezone-aware "now", used as the default for every timestamp column."""
    return datetime.now(timezone.utc)


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Account(Base):
    """An Instagram Business/Creator account connected through OAuth."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ig_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    account_type: Mapped[str | None] = mapped_column(String(64))
    profile_picture_url: Mapped[str | None] = mapped_column(Text)
    followers_count: Mapped[int | None] = mapped_column(Integer)
    media_count: Mapped[int | None] = mapped_column(Integer)

    # Long-lived access token, encrypted at rest (see app.crypto).
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    token_refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Only set when the Facebook Login flow is used.
    facebook_page_id: Mapped[str | None] = mapped_column(String(64))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_collected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    media: Mapped[list["Media"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )
    runs: Mapped[list["CollectionRun"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Account {self.username or self.ig_user_id}>"


class Media(Base):
    """A single post / Reel / story published by a connected account."""

    __tablename__ = "media"
    __table_args__ = (Index("ix_media_account_timestamp", "account_id", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    ig_media_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    media_type: Mapped[str | None] = mapped_column(String(32))
    media_product_type: Mapped[str | None] = mapped_column(String(32))
    caption: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    # Denormalised copy of the newest snapshot, so list views need one query.
    last_snapshot_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    latest_engagement_rate: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    account: Mapped[Account] = relationship(back_populates="media")
    snapshots: Mapped[list["MetricSnapshot"]] = relationship(
        back_populates="media",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MetricSnapshot.collected_at",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Media {self.ig_media_id} {self.media_product_type}>"


class MetricSnapshot(Base):
    """Metrics for one media item at one point in time.

    A new row is written on every collection run, which is what preserves the
    history after Meta stops serving the original numbers.
    """

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint("media_id", "collected_at", name="uq_snapshot_media_time"),
        Index("ix_snapshot_media_collected", "media_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"), index=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )

    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    saved: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    total_interactions: Mapped[int | None] = mapped_column(Integer)
    profile_visits: Mapped[int | None] = mapped_column(Integer)
    follows: Mapped[int | None] = mapped_column(Integer)

    engagement_rate: Mapped[float | None] = mapped_column(Float)
    # Denominator actually used for the engagement rate ("reach" or "followers").
    engagement_basis: Mapped[str | None] = mapped_column(String(16))

    # Untouched API payload, so nothing Meta returns is ever lost.
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    media: Mapped[Media] = relationship(back_populates="snapshots")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<MetricSnapshot media={self.media_id} at={self.collected_at}>"


class CollectionRun(Base):
    """Audit trail of every scheduled or manual collection."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=16), default=RunStatus.RUNNING
    )
    trigger: Mapped[str] = mapped_column(String(16), default="manual")

    media_seen: Mapped[int] = mapped_column(Integer, default=0)
    media_created: Mapped[int] = mapped_column(Integer, default=0)
    snapshots_created: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account | None] = relationship(back_populates="runs")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<CollectionRun {self.id} {self.status}>"
