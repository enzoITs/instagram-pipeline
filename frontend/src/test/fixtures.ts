import type {
  Account,
  AccountSummary,
  AnalysisData,
  CommentSample,
  Intent,
  MediaItem,
  Sentiment,
  TimeseriesPoint,
} from "../types";

const sentiments: Sentiment[] = ["positive", "negative", "neutral", "doubt", "spam"];
const intents: Intent[] = ["praise", "hater", "other", "question", "potential_buyer"];

export function makeComments(count: number): CommentSample[] {
  return Array.from({ length: count }, (_, index) => ({
    author_username: `@user${index}`,
    text: index === 0 ? "Adorei! Quando vende? Produto INCRÍVEL" : `Comentário número ${index}`,
    likes: 100 - index,
    created_at: "2026-09-01T12:00:00Z",
    sentiment_analysis: {
      sentiment: sentiments[index % sentiments.length],
      confidence: 0.91,
      intent_category: index === 0 ? "potential_buyer" : intents[index % intents.length],
      key_phrases: index === 0 ? ["vende", "produto incrível"] : ["número"],
    },
  }));
}

export function makeAnalysis(overrides: Partial<AnalysisData> = {}): AnalysisData {
  return {
    video_id: "7b1e3c1e-1111-4f5e-9a1a-1234567890ab",
    video: {
      instagram_id: "C8xYz12AbCd",
      url: "https://www.instagram.com/reel/C8xYz12AbCd/",
      media_type: "reel",
      owned: false,
      creator_username: "@brand",
      caption: "Novo produto!",
      thumbnail_url: null,
      published_at: "2026-08-15T10:30:00Z",
      duration_sec: 30,
    },
    metrics: {
      views: 250000,
      likes: 18500,
      comments: 1230,
      shares: null,
      saves: null,
      reach: null,
      engagement_rate: 7.89,
      engagement_basis: "views",
      engagement_score: 5.8,
    },
    tracked: false,
    history: [
      {
        collected_at: "2026-09-10T12:00:00Z",
        views: 250000,
        likes: 18500,
        comments: 1230,
        shares: null,
        saves: null,
        reach: null,
        engagement_rate: 7.89,
        engagement_basis: "views",
      },
    ],
    sentiment: { positive: 120, negative: 30, neutral: 25, doubt: 20, spam: 5 },
    intents: { potential_buyer: 25, hater: 10, praise: 110, question: 25, other: 30 },
    top_topics: [
      {
        topic: "produto incrível",
        frequency: 40,
        sentiment_avg: 0.92,
        key_keywords: ["produto", "incrível", "melhor"],
      },
      { topic: "entrega", frequency: 12, sentiment_avg: 0.2, key_keywords: ["entrega", "atraso"] },
    ],
    insights: [
      {
        type: "strength",
        title: "Alto potencial de conversão",
        description: "25 comentários demonstram intenção de compra.",
        impact_score: 8.4,
      },
      {
        type: "improvement",
        title: "Dúvidas recorrentes da audiência",
        description: "Responda as perguntas mais curtidas.",
        impact_score: 7.1,
      },
      { type: "trend", title: "Assunto dominante", description: "Produto incrível lidera.", impact_score: 5 },
    ],
    sentiment_comments_sample: makeComments(23),
    processing: {
      data_source: "apify",
      nlp_backend: "transformers",
      insights_source: "openai",
      comments_analyzed: 200,
      duration_seconds: 42.4,
      step_durations: { scraping: 20 },
      warnings: ["Coleta completa de comentários falhou; usando amostra do post."],
      completed_at: "2026-09-10T12:00:00Z",
    },
    ...overrides,
  };
}


export function makeAccount(overrides: Partial<Account> = {}): Account {
  return {
    id: 1,
    ig_user_id: "17841400000000001",
    username: "loja.demo",
    name: "Loja Demo",
    account_type: "BUSINESS",
    profile_picture_url: null,
    followers_count: 12400,
    media_count: 42,
    is_active: true,
    last_collected_at: "2026-09-10T12:00:00Z",
    token_expires_at: "2026-11-01T12:00:00Z",
    created_at: "2026-08-01T12:00:00Z",
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    account: makeAccount(),
    tracked_media: 24,
    snapshots: 168,
    total_likes: 31000,
    total_comments: 1800,
    total_saved: 2400,
    total_shares: 900,
    total_reach: 260000,
    total_views: 410000,
    average_engagement_rate: 13.12,
    engagement_basis: "reach",
    first_collected_at: "2026-08-02T12:00:00Z",
    last_collected_at: "2026-09-10T12:00:00Z",
    ...overrides,
  };
}

export function makeTimeseries(): TimeseriesPoint[] {
  return [
    {
      collected_at: "2026-09-09T12:00:00Z",
      likes: 1200,
      comments: 90,
      saved: 140,
      shares: 60,
      reach: 12000,
      views: 21000,
      engagement_rate: 12.42,
      engagement_basis: "reach",
      media_count: 24,
    },
    {
      collected_at: "2026-09-10T12:00:00Z",
      likes: 1500,
      comments: 110,
      saved: 160,
      shares: 70,
      reach: 13000,
      views: 23000,
      engagement_rate: 13.38,
      engagement_basis: "reach",
      media_count: 24,
    },
  ];
}

export function makeMedia(overrides: Partial<MediaItem> = {}): MediaItem {
  return {
    id: 7,
    ig_media_id: "media-reel",
    media_type: "VIDEO",
    media_product_type: "REELS",
    caption: "Reels sobre métricas",
    permalink: "https://www.instagram.com/reel/C8xYz12AbCd/",
    thumbnail_url: null,
    media_url: null,
    timestamp: "2026-09-08T12:00:00Z",
    last_snapshot_at: "2026-09-10T12:00:00Z",
    latest_engagement_rate: 13.5,
    latest: {
      id: 21,
      collected_at: "2026-09-10T12:00:00Z",
      likes: 200,
      comments: 20,
      saved: 40,
      shares: 10,
      reach: 2000,
      views: 6000,
      total_interactions: 270,
      engagement_rate: 13.5,
      engagement_basis: "reach",
    },
    ...overrides,
  };
}
