/** Mirrors the backend contracts in backend/app/schemas.py. */

export type Sentiment = "positive" | "negative" | "neutral" | "doubt" | "spam";
export type Intent = "potential_buyer" | "hater" | "praise" | "question" | "other";
export type InsightType = "strength" | "improvement" | "trend";
export type VideoStatus = "processing" | "completed" | "failed";

export interface VideoInfo {
  instagram_id: string;
  url: string;
  media_type: string | null;
  creator_username: string | null;
  caption: string | null;
  thumbnail_url: string | null;
  published_at: string | null;
  duration_sec: number | null;
}

export interface Metrics {
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
  engagement_rate: number | null;
  engagement_score: number | null;
}

export type SentimentDistribution = Record<Sentiment, number>;
export type IntentDistribution = Record<Intent, number>;

export interface Topic {
  topic: string;
  frequency: number;
  sentiment_avg: number;
  key_keywords: string[];
}

export interface Insight {
  type: InsightType;
  title: string;
  description: string;
  impact_score: number;
}

export interface CommentSentiment {
  sentiment: Sentiment;
  confidence: number;
  intent_category: Intent;
  key_phrases: string[];
}

export interface CommentSample {
  author_username: string | null;
  text: string;
  likes: number;
  created_at: string | null;
  sentiment_analysis: CommentSentiment | null;
}

export interface ProcessingInfo {
  data_source: string | null;
  nlp_backend: string | null;
  insights_source: string | null;
  comments_analyzed: number;
  duration_seconds: number | null;
  step_durations: Record<string, number>;
  warnings: string[];
  completed_at: string | null;
}

export interface AnalysisData {
  video_id: string;
  video: VideoInfo;
  metrics: Metrics;
  sentiment: SentimentDistribution;
  intents: IntentDistribution;
  top_topics: Topic[];
  insights: Insight[];
  sentiment_comments_sample: CommentSample[];
  processing: ProcessingInfo;
}

export interface AnalyzeAcceptedResponse {
  status: "processing";
  video_id: string;
  message: string;
}

export interface AnalyzeCachedResponse {
  status: "success";
  video_id: string;
  cached: true;
  data: AnalysisData;
}

export type AnalyzeResponse = AnalyzeAcceptedResponse | AnalyzeCachedResponse;

export interface AnalysisProcessingResponse {
  status: "processing";
  video_id: string;
  message: string;
  step: number;
  total_steps: number;
}

export interface AnalysisSuccessResponse {
  status: "success";
  data: AnalysisData;
}

export interface AnalysisErrorResponse {
  status: "error";
  video_id: string;
  message: string;
}

export type AnalysisStatusResponse =
  AnalysisProcessingResponse | AnalysisSuccessResponse | AnalysisErrorResponse;

export interface RecentItem {
  id: string;
  instagram_id: string;
  url: string | null;
  creator_username: string | null;
  status: VideoStatus;
  created_at: string;
  thumbnail_url: string | null;
  engagement_score: number | null;
}

export interface RecentResponse {
  status: "success";
  data: RecentItem[];
  pagination: { total: number; limit: number; offset: number; page: number };
}

export interface ApiErrorBody {
  status: "error";
  error: { code: string; message: string; error_id?: string };
}
