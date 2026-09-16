import type { AnalysisData, CommentSample, Intent, Sentiment } from "../types";

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
      engagement_rate: 7.89,
      engagement_score: 5.8,
    },
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
