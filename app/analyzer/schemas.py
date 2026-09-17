"""Pydantic request/response contracts for the public API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.analyzer.models import InsightType, IntentCategory, SentimentLabel, VideoStatus
from app.instagram.url import InvalidInstagramURLError, parse_instagram_url

TOTAL_STEPS = 8
STEP_LABELS: dict[int, str] = {
    0: "Na fila de processamento",
    1: "Preparando análise",
    2: "Coletando dados do Instagram",
    3: "Salvando métricas e comentários",
    4: "Analisando sentimento, intenção e tópicos",
    5: "Salvando resultados da análise",
    6: "Calculando métricas de engajamento",
    7: "Gerando insights com IA",
    8: "Finalizando e armazenando em cache",
}


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes (e.g. from SQLite) are UTC by convention in this codebase."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# --------------------------------------------------------------------------- requests
class AnalyzeRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Link público de um Reel ou Post do Instagram.",
        examples=["https://www.instagram.com/reel/C8xYz12AbCd/"],
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        try:
            return parse_instagram_url(value).canonical_url
        except InvalidInstagramURLError as exc:
            raise ValueError(str(exc)) from exc

    @property
    def instagram_id(self) -> str:
        return parse_instagram_url(self.url).shortcode

    @property
    def media_type(self) -> str:
        return parse_instagram_url(self.url).media_type


# --------------------------------------------------------------------------- analysis payload
class VideoOut(APIModel):
    instagram_id: str
    url: str
    media_type: str | None = None
    owned: bool = Field(
        default=False, description="Post de uma conta conectada: métricas vêm da API oficial."
    )
    creator_username: str | None = Field(default=None, examples=["@brand"])
    caption: str | None = None
    thumbnail_url: str | None = None
    published_at: UTCDateTime | None = None
    duration_sec: float | None = None


class MetricsOut(APIModel):
    views: int | None = Field(default=None, examples=[250000])
    likes: int | None = Field(default=None, examples=[18500])
    comments: int | None = Field(default=None, examples=[1230])
    shares: int | None = Field(default=None, description="Só visível ao dono do post (null).")
    saves: int | None = Field(default=None, description="Só visível ao dono do post (null).")
    reach: int | None = Field(default=None, description="Só visível ao dono do post (null).")
    engagement_rate: float | None = Field(default=None, description="Percentual (%).", examples=[8.67])
    engagement_basis: Literal["reach", "views", "followers"] | None = Field(
        default=None, description="Denominador usado na taxa."
    )
    engagement_score: float | None = Field(default=None, ge=0, le=10, examples=[8.9])


class MetricPointOut(APIModel):
    """One point of a tracked link's metric history."""

    collected_at: UTCDateTime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    reach: int | None = None
    engagement_rate: float | None = None
    engagement_basis: str | None = None


class SentimentDistribution(APIModel):
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    doubt: int = 0
    spam: int = 0


class IntentDistribution(APIModel):
    potential_buyer: int = 0
    hater: int = 0
    praise: int = 0
    question: int = 0
    other: int = 0


class TopicOut(APIModel):
    topic: str
    frequency: int
    sentiment_avg: float = Field(ge=0, le=1, description="0 = negativo, 0.5 = neutro, 1 = positivo")
    key_keywords: list[str] = Field(default_factory=list)


class InsightOut(APIModel):
    type: InsightType
    title: str
    description: str
    impact_score: float = Field(ge=0, le=10)


class SentimentAnalysisOut(APIModel):
    sentiment: SentimentLabel
    confidence: float = Field(ge=0, le=1)
    intent_category: IntentCategory
    key_phrases: list[str] = Field(default_factory=list)


class CommentOut(APIModel):
    author_username: str | None = Field(default=None, examples=["@user123"])
    text: str
    likes: int = 0
    created_at: UTCDateTime | None = None
    sentiment_analysis: SentimentAnalysisOut | None = None


class ProcessingInfo(APIModel):
    data_source: str | None = Field(default=None, description="apify | instaloader | mock")
    nlp_backend: str | None = None
    insights_source: str | None = None
    comments_analyzed: int = 0
    duration_seconds: float | None = None
    step_durations: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    completed_at: UTCDateTime | None = None


class TrackingRequest(BaseModel):
    tracked: bool = Field(description="true acompanha a evolução das métricas deste link.")


class TrackingResponse(APIModel):
    status: Literal["success"] = "success"
    video_id: UUID
    tracked: bool


class AnalysisData(APIModel):
    video_id: UUID
    video: VideoOut
    metrics: MetricsOut
    tracked: bool = False
    history: list[MetricPointOut] = Field(
        default_factory=list, description="Snapshots de métricas, do mais antigo ao mais recente."
    )
    sentiment: SentimentDistribution
    intents: IntentDistribution
    top_topics: list[TopicOut]
    insights: list[InsightOut]
    sentiment_comments_sample: list[CommentOut]
    processing: ProcessingInfo


# --------------------------------------------------------------------------- responses
class AnalyzeAcceptedResponse(APIModel):
    status: Literal["processing"] = "processing"
    video_id: UUID
    message: str = "Análise iniciada. Resultado disponível em 2-5 minutos."


class AnalyzeCachedResponse(APIModel):
    status: Literal["success"] = "success"
    video_id: UUID
    cached: bool = True
    data: AnalysisData


class AnalysisProcessingResponse(APIModel):
    status: Literal["processing"] = "processing"
    video_id: UUID
    message: str
    step: int = Field(ge=0, le=TOTAL_STEPS)
    total_steps: int = TOTAL_STEPS


class AnalysisSuccessResponse(APIModel):
    status: Literal["success"] = "success"
    data: AnalysisData


class AnalysisErrorResponse(APIModel):
    status: Literal["error"] = "error"
    video_id: UUID
    message: str


class RecentItem(APIModel):
    id: UUID
    instagram_id: str
    url: str | None = None
    creator_username: str | None = None
    status: VideoStatus
    created_at: UTCDateTime
    thumbnail_url: str | None = None
    engagement_score: float | None = None


class Pagination(APIModel):
    total: int
    limit: int
    offset: int
    page: int


class RecentResponse(APIModel):
    status: Literal["success"] = "success"
    data: list[RecentItem]
    pagination: Pagination


class DeleteResponse(APIModel):
    status: Literal["deleted"] = "deleted"
    message: str = "Análise removida com sucesso"


class CacheStats(APIModel):
    hits: int = 0
    misses: int = 0
    hit_ratio: float | None = None


class HealthResponse(APIModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "disconnected"]
    redis: Literal["connected", "disconnected"]
    timestamp: UTCDateTime
    version: str
    cache: CacheStats | None = None


class ErrorDetail(APIModel):
    code: str
    message: str
    error_id: str | None = None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(APIModel):
    status: Literal["error"] = "error"
    error: ErrorDetail


def with_at(username: str | None) -> str | None:
    """Usernames are stored bare and exposed with a leading '@'."""
    if not username:
        return None
    return username if username.startswith("@") else f"@{username}"
