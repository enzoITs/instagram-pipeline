"""ORM models for link analysis: videos, comments, NLP results, insights, cache.

They share `app.database.Base` with the account models, so one migration chain
and one database hold both halves of the product. PostgreSQL-specific types
(JSONB, TEXT[]) carry SQLite variants so the test suite runs without a server.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, UTCDateTime
from app.models import utcnow

if TYPE_CHECKING:
    from app.models import Media

StringArray = ARRAY(Text()).with_variant(JSON(), "sqlite")
JSONDocument = JSONB().with_variant(JSON(), "sqlite")


class VideoStatus(enum.StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SentimentLabel(enum.StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    DOUBT = "doubt"
    SPAM = "spam"


class IntentCategory(enum.StrEnum):
    POTENTIAL_BUYER = "potential_buyer"
    HATER = "hater"
    PRAISE = "praise"
    QUESTION = "question"
    OTHER = "other"


class InsightType(enum.StrEnum):
    STRENGTH = "strength"
    IMPROVEMENT = "improvement"
    TREND = "trend"


def _str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint instead of a native PG enum (painless migrations)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instagram_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(16))
    caption: Mapped[str | None] = mapped_column(Text)
    creator_username: Mapped[str | None] = mapped_column(String(128))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    status: Mapped[VideoStatus] = mapped_column(
        _str_enum(VideoStatus, "ck_videos_status"), nullable=False, default=VideoStatus.PROCESSING
    )
    current_step: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    # "apify", "instaloader", "mock", "graph_api" or "graph_api+<scraper>".
    data_source: Mapped[str | None] = mapped_column(String(32))
    task_id: Mapped[str | None] = mapped_column(String(64))
    processing_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    # Set when the link points at a post of a connected account.
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    # Tracked links get a fresh metrics snapshot periodically (Celery beat).
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_tracked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    metrics: Mapped[EngagementMetrics | None] = relationship(
        back_populates="video", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="video", cascade="all, delete-orphan", passive_deletes=True
    )
    topics: Mapped[list[TopicAnalysis]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(TopicAnalysis.frequency)",
    )
    insights: Mapped[list[Insight]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(Insight.impact_score)",
    )
    snapshots: Mapped[list[VideoMetricSnapshot]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="VideoMetricSnapshot.collected_at",
    )
    media: Mapped[Media | None] = relationship()

    def __repr__(self) -> str:
        return f"<Video {self.instagram_id} status={self.status}>"


class EngagementMetrics(Base):
    __tablename__ = "engagement_metrics"
    __table_args__ = (
        CheckConstraint(
            "engagement_score IS NULL OR (engagement_score >= 0 AND engagement_score <= 10)",
            name="ck_engagement_metrics_engagement_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Counters are nullable on purpose: Instagram hides some of them (shares,
    # saves and reach are only visible to the account owner). NULL means "not
    # available", not 0.
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments_count: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    reach: Mapped[int | None] = mapped_column(BigInteger)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    # Denominator used for the rate: "reach", "views" or "followers".
    engagement_basis: Mapped[str | None] = mapped_column(String(16))
    engagement_score: Mapped[float | None] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="metrics")


class VideoMetricSnapshot(Base):
    """Metrics of a tracked link at one point in time (history for any public post)."""

    __tablename__ = "video_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("video_id", "collected_at", name="uq_video_metric_snapshots_video_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    source: Mapped[str | None] = mapped_column(String(32))
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments_count: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    reach: Mapped[int | None] = mapped_column(BigInteger)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    engagement_basis: Mapped[str | None] = mapped_column(String(16))

    video: Mapped[Video] = relationship(back_populates="snapshots")


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        # Provider ids are only guaranteed unique within a post, and the hashed
        # fallback ids could collide across posts.
        UniqueConstraint("video_id", "comment_id", name="uq_comments_video_comment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    comment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scraped_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="comments")
    sentiment: Mapped[SentimentAnalysis | None] = relationship(
        back_populates="comment", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analysis"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_sentiment_analysis_confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    sentiment: Mapped[SentimentLabel] = mapped_column(
        _str_enum(SentimentLabel, "ck_sentiment_analysis_sentiment"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    intent_category: Mapped[IntentCategory] = mapped_column(
        _str_enum(IntentCategory, "ck_sentiment_analysis_intent_category"), nullable=False
    )
    key_phrases: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    comment: Mapped[Comment] = relationship(back_populates="sentiment")


class TopicAnalysis(Base):
    __tablename__ = "topic_analysis"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment_avg: Mapped[float] = mapped_column(Float, nullable=False)
    key_keywords: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="topics")


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (
        CheckConstraint("impact_score >= 0 AND impact_score <= 10", name="ck_insights_impact_score_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[InsightType] = mapped_column(_str_enum(InsightType, "ck_insights_type"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rules")
    generated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="insights")


class AnalysisCache(Base):
    """Durable backup of the Redis analysis cache (survives Redis restarts/flushes)."""

    __tablename__ = "analysis_cache"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_instagram_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    analysis_data: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


# Explicit secondary indexes (unique constraints above add their own indexes).
Index("ix_videos_created_at", Video.created_at.desc())
Index("ix_videos_tracked", Video.tracked)
Index("ix_comments_video_id_likes", Comment.video_id, Comment.likes.desc())
Index("ix_topic_analysis_video_id_frequency", TopicAnalysis.video_id, TopicAnalysis.frequency.desc())
Index("ix_insights_video_id_impact_score", Insight.video_id, Insight.impact_score.desc())
Index("ix_analysis_cache_expires_at", AnalysisCache.expires_at)

__all__ = [
    "AnalysisCache",
    "Comment",
    "EngagementMetrics",
    "Insight",
    "InsightType",
    "IntentCategory",
    "SentimentAnalysis",
    "SentimentLabel",
    "TopicAnalysis",
    "Video",
    "VideoMetricSnapshot",
    "VideoStatus",
    "utcnow",
]
