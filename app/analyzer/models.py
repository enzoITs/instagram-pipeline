"""SQLAlchemy ORM models (7 tables).

PostgreSQL-specific types (UUID, JSONB, TEXT[]) are declared with SQLite variants so
the unit-test suite can run without a database server; production always runs on
PostgreSQL 15.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

StringArray = ARRAY(Text()).with_variant(JSON(), "sqlite")
JSONDocument = JSONB().with_variant(JSON(), "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    status: Mapped[VideoStatus] = mapped_column(
        _str_enum(VideoStatus, "status"), nullable=False, default=VideoStatus.PROCESSING
    )
    current_step: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(16))
    task_id: Mapped[str | None] = mapped_column(String(64))
    processing_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    def __repr__(self) -> str:
        return f"<Video {self.instagram_id} status={self.status}>"


class EngagementMetrics(Base):
    __tablename__ = "engagement_metrics"
    __table_args__ = (
        CheckConstraint(
            "engagement_score IS NULL OR (engagement_score >= 0 AND engagement_score <= 10)",
            name="engagement_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Counters are nullable on purpose: Instagram hides some of them (e.g. shares and
    # saves are only visible to the account owner). NULL means "not available", not 0.
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments_count: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    engagement_score: Mapped[float | None] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="metrics")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    comment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    author: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="comments")
    sentiment: Mapped[SentimentAnalysis | None] = relationship(
        back_populates="comment", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analysis"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    sentiment: Mapped[SentimentLabel] = mapped_column(
        _str_enum(SentimentLabel, "sentiment"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    intent_category: Mapped[IntentCategory] = mapped_column(
        _str_enum(IntentCategory, "intent_category"), nullable=False
    )
    key_phrases: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
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
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="topics")


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (
        CheckConstraint("impact_score >= 0 AND impact_score <= 10", name="impact_score_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[InsightType] = mapped_column(_str_enum(InsightType, "type"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rules")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="insights")


class AnalysisCache(Base):
    """Durable backup of the Redis analysis cache (survives Redis restarts/flushes)."""

    __tablename__ = "analysis_cache"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_instagram_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    analysis_data: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


# Explicit secondary indexes (unique constraints above add their own indexes).
Index("ix_videos_created_at", Video.created_at.desc())
Index("ix_comments_video_id_likes", Comment.video_id, Comment.likes.desc())
Index("ix_topic_analysis_video_id_frequency", TopicAnalysis.video_id, TopicAnalysis.frequency.desc())
Index("ix_insights_video_id_impact_score", Insight.video_id, Insight.impact_score.desc())
Index("ix_analysis_cache_expires_at", AnalysisCache.expires_at)

__all__ = [
    "AnalysisCache",
    "Base",
    "Comment",
    "EngagementMetrics",
    "Insight",
    "InsightType",
    "IntentCategory",
    "SentimentAnalysis",
    "SentimentLabel",
    "TopicAnalysis",
    "Video",
    "VideoStatus",
    "utcnow",
]
