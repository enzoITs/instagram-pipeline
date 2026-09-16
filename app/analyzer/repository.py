"""Queries shared by the API (async session) and the worker (sync session).

Statements are built once here; only their execution differs between the two worlds,
so the analysis payload is assembled by exactly one function (`build_analysis_data`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.analyzer.models import (
    AnalysisCache,
    Comment,
    EngagementMetrics,
    SentimentAnalysis,
    Video,
)
from app.analyzer.schemas import (
    AnalysisData,
    CommentOut,
    InsightOut,
    IntentDistribution,
    MetricPointOut,
    MetricsOut,
    ProcessingInfo,
    SentimentAnalysisOut,
    SentimentDistribution,
    TopicOut,
    VideoOut,
    with_at,
)

SAMPLE_PER_SENTIMENT = 10  # up to 50 comments: enough for the dashboard, small payload


# --------------------------------------------------------------------------- statements
def video_statement(video_id: uuid.UUID) -> Select[Any]:
    return (
        select(Video)
        .options(
            selectinload(Video.metrics),
            selectinload(Video.topics),
            selectinload(Video.insights),
            selectinload(Video.snapshots),
        )
        .where(Video.id == video_id)
    )


def distribution_statement(video_id: uuid.UUID, column: Any) -> Select[Any]:
    return (
        select(column, func.count())
        .join(Comment, SentimentAnalysis.comment_id == Comment.id)
        .where(Comment.video_id == video_id)
        .group_by(column)
    )


def sample_statement(video_id: uuid.UUID, per_sentiment: int = SAMPLE_PER_SENTIMENT) -> Select[Any]:
    """Most-liked comments per sentiment (window function, one round trip)."""
    ranked = (
        select(
            Comment.id.label("comment_pk"),
            func.row_number()
            .over(partition_by=SentimentAnalysis.sentiment, order_by=(Comment.likes.desc(), Comment.id))
            .label("position"),
        )
        .join(SentimentAnalysis, SentimentAnalysis.comment_id == Comment.id)
        .where(Comment.video_id == video_id)
        .subquery()
    )
    return (
        select(Comment, SentimentAnalysis)
        .join(SentimentAnalysis, SentimentAnalysis.comment_id == Comment.id)
        .join(ranked, ranked.c.comment_pk == Comment.id)
        .where(ranked.c.position <= per_sentiment)
        .order_by(Comment.likes.desc(), Comment.id)
    )


def recent_statement(limit: int, offset: int) -> Select[Any]:
    return (
        select(
            Video.id,
            Video.instagram_id,
            Video.url,
            Video.creator_username,
            Video.status,
            Video.created_at,
            Video.thumbnail_url,
            EngagementMetrics.engagement_score,
        )
        .outerjoin(EngagementMetrics, EngagementMetrics.video_id == Video.id)
        .order_by(Video.created_at.desc(), Video.id)
        .limit(limit)
        .offset(offset)
    )


# --------------------------------------------------------------------------- payload
def build_analysis_data(
    video: Video,
    sentiment_rows: Sequence[Any],
    intent_rows: Sequence[Any],
    sample_rows: Sequence[Any],
) -> AnalysisData:
    stats: dict[str, Any] = video.processing_stats or {}
    metrics = video.metrics
    return AnalysisData(
        video_id=video.id,
        video=VideoOut(
            instagram_id=video.instagram_id,
            url=video.url,
            media_type=video.media_type,
            owned=video.media_id is not None,
            creator_username=with_at(video.creator_username),
            caption=video.caption,
            thumbnail_url=video.thumbnail_url,
            published_at=video.published_at,
            duration_sec=video.duration_sec,
        ),
        metrics=MetricsOut(
            views=metrics.views if metrics else None,
            likes=metrics.likes if metrics else None,
            comments=metrics.comments_count if metrics else None,
            shares=metrics.shares if metrics else None,
            saves=metrics.saves if metrics else None,
            reach=metrics.reach if metrics else None,
            engagement_rate=metrics.engagement_rate if metrics else None,
            engagement_basis=metrics.engagement_basis if metrics else None,
            engagement_score=metrics.engagement_score if metrics else None,
        ),
        tracked=bool(video.tracked),
        history=[
            MetricPointOut(
                collected_at=snapshot.collected_at,
                views=snapshot.views,
                likes=snapshot.likes,
                comments=snapshot.comments_count,
                shares=snapshot.shares,
                saves=snapshot.saves,
                reach=snapshot.reach,
                engagement_rate=snapshot.engagement_rate,
                engagement_basis=snapshot.engagement_basis,
            )
            for snapshot in sorted(video.snapshots, key=lambda row: row.collected_at)
        ],
        sentiment=SentimentDistribution(**{str(label): int(count) for label, count in sentiment_rows}),
        intents=IntentDistribution(**{str(label): int(count) for label, count in intent_rows}),
        top_topics=[
            TopicOut(
                topic=topic.topic,
                frequency=topic.frequency,
                sentiment_avg=topic.sentiment_avg,
                key_keywords=list(topic.key_keywords or []),
            )
            for topic in video.topics
        ],
        insights=[
            InsightOut(
                type=insight.type,
                title=insight.title,
                description=insight.description,
                impact_score=insight.impact_score,
            )
            for insight in video.insights
        ],
        sentiment_comments_sample=[
            CommentOut(
                author_username=with_at(comment.author),
                text=comment.text,
                likes=comment.likes,
                created_at=comment.created_at,
                sentiment_analysis=SentimentAnalysisOut(
                    sentiment=analysis.sentiment,
                    confidence=analysis.confidence,
                    intent_category=analysis.intent_category,
                    key_phrases=list(analysis.key_phrases or []),
                ),
            )
            for comment, analysis in sample_rows
        ],
        processing=ProcessingInfo(
            data_source=video.data_source,
            nlp_backend=stats.get("nlp_backend"),
            insights_source=stats.get("insights_source"),
            comments_analyzed=int(stats.get("comments_analyzed") or 0),
            duration_seconds=stats.get("duration_seconds"),
            step_durations=stats.get("step_durations") or {},
            warnings=stats.get("warnings") or [],
            completed_at=video.completed_at,
        ),
    )


def load_analysis_sync(session: Session, video_id: uuid.UUID) -> AnalysisData | None:
    video = session.scalars(video_statement(video_id)).first()
    if video is None:
        return None
    return build_analysis_data(
        video,
        session.execute(distribution_statement(video_id, SentimentAnalysis.sentiment)).all(),
        session.execute(distribution_statement(video_id, SentimentAnalysis.intent_category)).all(),
        session.execute(sample_statement(video_id)).all(),
    )


async def load_analysis_async(session: AsyncSession, video_id: uuid.UUID) -> AnalysisData | None:
    video = (await session.scalars(video_statement(video_id))).first()
    if video is None:
        return None
    return build_analysis_data(
        video,
        (await session.execute(distribution_statement(video_id, SentimentAnalysis.sentiment))).all(),
        (await session.execute(distribution_statement(video_id, SentimentAnalysis.intent_category))).all(),
        (await session.execute(sample_statement(video_id))).all(),
    )


# --------------------------------------------------------------------------- cache backup
def upsert_cache_backup(session: Session, instagram_id: str, data: dict[str, Any], ttl_seconds: int) -> None:
    """Write (or replace) the durable copy of one analysis.

    Two workers can reach this at the same time for the same post, so the insert
    is tried inside a savepoint and a lost race falls through to the update
    instead of blowing up the surrounding transaction.
    """
    now = datetime.now(timezone.utc)
    values = {
        "analysis_data": data,
        "ttl_minutes": max(1, ttl_seconds // 60),
        "expires_at": now + timedelta(seconds=ttl_seconds),
        "updated_at": now,
    }
    row = session.scalars(select(AnalysisCache).where(AnalysisCache.video_instagram_id == instagram_id)).first()
    if row is None:
        try:
            with session.begin_nested():
                session.add(AnalysisCache(video_instagram_id=instagram_id, **values))
            return
        except IntegrityError:
            row = session.scalars(
                select(AnalysisCache).where(AnalysisCache.video_instagram_id == instagram_id)
            ).one()
    for field, value in values.items():
        setattr(row, field, value)


async def get_cache_backup(session: AsyncSession, instagram_id: str) -> dict[str, Any] | None:
    row = (
        await session.scalars(
            select(AnalysisCache).where(
                AnalysisCache.video_instagram_id == instagram_id,
                AnalysisCache.expires_at > datetime.now(timezone.utc),
            )
        )
    ).first()
    return dict(row.analysis_data) if row is not None else None


async def delete_cache_backup(session: AsyncSession, instagram_id: str) -> None:
    await session.execute(delete(AnalysisCache).where(AnalysisCache.video_instagram_id == instagram_id))
