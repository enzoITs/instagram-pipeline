"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-10 22:06:17.682890

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(name))


def upgrade() -> None:
    op.create_table(
        "analysis_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_instagram_id", sa.String(length=64), nullable=False),
        sa.Column("analysis_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ttl_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_cache")),
        sa.UniqueConstraint("video_instagram_id", name=op.f("uq_analysis_cache_video_instagram_id")),
    )
    op.create_index("ix_analysis_cache_expires_at", "analysis_cache", ["expires_at"], unique=False)

    op.create_table(
        "videos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instagram_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("creator_username", sa.String(length=128), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processing"),
        sa.Column("current_step", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("processing_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_check("status", ("processing", "completed", "failed"), "ck_videos_status"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_videos")),
        sa.UniqueConstraint("instagram_id", name=op.f("uq_videos_instagram_id")),
    )
    op.create_index("ix_videos_created_at", "videos", [sa.literal_column("created_at DESC")], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_comments_video_id_videos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
        sa.UniqueConstraint("comment_id", name=op.f("uq_comments_comment_id")),
    )
    op.create_index(
        "ix_comments_video_id_likes", "comments", ["video_id", sa.literal_column("likes DESC")], unique=False
    )

    op.create_table(
        "engagement_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments_count", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("saves", sa.BigInteger(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "engagement_score IS NULL OR (engagement_score >= 0 AND engagement_score <= 10)",
            name=op.f("ck_engagement_metrics_engagement_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_engagement_metrics_video_id_videos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engagement_metrics")),
        sa.UniqueConstraint("video_id", name=op.f("uq_engagement_metrics_video_id")),
    )

    op.create_table(
        "insights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="rules"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _enum_check("type", ("strength", "improvement", "trend"), "ck_insights_type"),
        sa.CheckConstraint("impact_score >= 0 AND impact_score <= 10", name=op.f("ck_insights_impact_score_range")),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_insights_video_id_videos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights")),
    )
    op.create_index(
        "ix_insights_video_id_impact_score",
        "insights",
        ["video_id", sa.literal_column("impact_score DESC")],
        unique=False,
    )

    op.create_table(
        "topic_analysis",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.Column("sentiment_avg", sa.Float(), nullable=False),
        sa.Column("key_keywords", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_topic_analysis_video_id_videos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_analysis")),
    )
    op.create_index(
        "ix_topic_analysis_video_id_frequency",
        "topic_analysis",
        ["video_id", sa.literal_column("frequency DESC")],
        unique=False,
    )

    op.create_table(
        "sentiment_analysis",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("sentiment", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("intent_category", sa.String(length=20), nullable=False),
        sa.Column("key_phrases", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _enum_check(
            "sentiment", ("positive", "negative", "neutral", "doubt", "spam"), "ck_sentiment_analysis_sentiment"
        ),
        _enum_check(
            "intent_category",
            ("potential_buyer", "hater", "praise", "question", "other"),
            "ck_sentiment_analysis_intent_category",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name=op.f("ck_sentiment_analysis_confidence_range")),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["comments.id"],
            name=op.f("fk_sentiment_analysis_comment_id_comments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentiment_analysis")),
        sa.UniqueConstraint("comment_id", name=op.f("uq_sentiment_analysis_comment_id")),
    )


def downgrade() -> None:
    op.drop_table("sentiment_analysis")
    op.drop_index("ix_topic_analysis_video_id_frequency", table_name="topic_analysis")
    op.drop_table("topic_analysis")
    op.drop_index("ix_insights_video_id_impact_score", table_name="insights")
    op.drop_table("insights")
    op.drop_table("engagement_metrics")
    op.drop_index("ix_comments_video_id_likes", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_videos_created_at", table_name="videos")
    op.drop_table("videos")
    op.drop_index("ix_analysis_cache_expires_at", table_name="analysis_cache")
    op.drop_table("analysis_cache")
