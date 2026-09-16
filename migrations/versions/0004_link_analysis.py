"""link analysis tables

Brings the schema of the former standalone Instagram_DataAnlise project into
this database, with the integration changes applied from the start:

* `videos.media_id` links an analysed URL to a post of a connected account;
* `videos.tracked` / `video_metric_snapshots` keep a metrics history for links;
* `engagement_metrics.reach` / `engagement_basis` follow the shared formula;
* `comments.comment_id` is unique per video instead of globally;
* `videos.data_source` fits combined sources such as "graph_api+apify".

Types are portable: JSONB/TEXT[] on PostgreSQL, JSON on SQLite (tests).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSONDocument = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
StringArray = postgresql.ARRAY(sa.Text()).with_variant(sa.JSON(), "sqlite")
NOW = sa.func.now()


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def upgrade() -> None:
    # --- account side: weightable engagement averages and one run at a time ---
    op.add_column("metric_snapshots", sa.Column("engagement_denominator", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE metric_snapshots SET engagement_denominator = reach "
        "WHERE engagement_basis = 'reach' AND reach IS NOT NULL"
    )
    # Two simultaneous requests could both start a collection for one account;
    # this index makes the database decide, so the loser gets a 409.
    op.execute(
        "UPDATE collection_runs SET status = 'FAILED', "
        "error = 'Closed when the one-run-per-account rule was introduced.' "
        "WHERE status = 'RUNNING' AND id NOT IN "
        "(SELECT MAX(id) FROM collection_runs WHERE status = 'RUNNING' GROUP BY account_id)"
    )
    op.create_index(
        "uq_collection_runs_one_running_per_account",
        "collection_runs",
        ["account_id"],
        unique=True,
        sqlite_where=sa.text("status = 'RUNNING'"),
        postgresql_where=sa.text("status = 'RUNNING'"),
    )

    # --- link analysis ---
    op.create_table(
        "analysis_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_instagram_id", sa.String(length=64), nullable=False),
        sa.Column("analysis_data", JSONDocument, nullable=False),
        sa.Column("ttl_minutes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_cache"),
        sa.UniqueConstraint("video_instagram_id", name="uq_analysis_cache_video_instagram_id"),
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
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_step", sa.SmallInteger(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("data_source", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("processing_stats", JSONDocument, nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("tracked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_tracked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_check("status", ("processing", "completed", "failed"), "ck_videos_status"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], name="fk_videos_media_id_media", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_videos"),
        sa.UniqueConstraint("instagram_id", name="uq_videos_instagram_id"),
    )
    op.create_index("ix_videos_created_at", "videos", [sa.literal_column("created_at DESC")], unique=False)
    op.create_index("ix_videos_tracked", "videos", ["tracked"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], name="fk_comments_video_id_videos", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_comments"),
        sa.UniqueConstraint("video_id", "comment_id", name="uq_comments_video_comment"),
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
        sa.Column("reach", sa.BigInteger(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column("engagement_basis", sa.String(length=16), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "engagement_score IS NULL OR (engagement_score >= 0 AND engagement_score <= 10)",
            name="ck_engagement_metrics_engagement_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name="fk_engagement_metrics_video_id_videos", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_engagement_metrics"),
        sa.UniqueConstraint("video_id", name="uq_engagement_metrics_video_id"),
    )

    op.create_table(
        "video_metric_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments_count", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("saves", sa.BigInteger(), nullable=True),
        sa.Column("reach", sa.BigInteger(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column("engagement_basis", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name="fk_video_metric_snapshots_video_id_videos", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_video_metric_snapshots"),
        sa.UniqueConstraint("video_id", "collected_at", name="uq_video_metric_snapshots_video_time"),
    )
    op.create_index(
        "ix_video_metric_snapshots_video_id", "video_metric_snapshots", ["video_id"], unique=False
    )

    op.create_table(
        "insights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        _enum_check("type", ("strength", "improvement", "trend"), "ck_insights_type"),
        sa.CheckConstraint("impact_score >= 0 AND impact_score <= 10", name="ck_insights_impact_score_range"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], name="fk_insights_video_id_videos", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_insights"),
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
        sa.Column("key_keywords", StringArray, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name="fk_topic_analysis_video_id_videos", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_topic_analysis"),
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
        sa.Column("key_phrases", StringArray, nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        _enum_check(
            "sentiment", ("positive", "negative", "neutral", "doubt", "spam"), "ck_sentiment_analysis_sentiment"
        ),
        _enum_check(
            "intent_category",
            ("potential_buyer", "hater", "praise", "question", "other"),
            "ck_sentiment_analysis_intent_category",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_sentiment_analysis_confidence_range"),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["comments.id"],
            name="fk_sentiment_analysis_comment_id_comments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sentiment_analysis"),
        sa.UniqueConstraint("comment_id", name="uq_sentiment_analysis_comment_id"),
    )


def downgrade() -> None:
    op.drop_index("uq_collection_runs_one_running_per_account", table_name="collection_runs")
    op.drop_column("metric_snapshots", "engagement_denominator")
    op.drop_table("sentiment_analysis")
    op.drop_index("ix_topic_analysis_video_id_frequency", table_name="topic_analysis")
    op.drop_table("topic_analysis")
    op.drop_index("ix_insights_video_id_impact_score", table_name="insights")
    op.drop_table("insights")
    op.drop_index("ix_video_metric_snapshots_video_id", table_name="video_metric_snapshots")
    op.drop_table("video_metric_snapshots")
    op.drop_table("engagement_metrics")
    op.drop_index("ix_comments_video_id_likes", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_videos_tracked", table_name="videos")
    op.drop_index("ix_videos_created_at", table_name="videos")
    op.drop_table("videos")
    op.drop_index("ix_analysis_cache_expires_at", table_name="analysis_cache")
    op.drop_table("analysis_cache")
