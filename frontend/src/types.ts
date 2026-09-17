/** Mirrors the backend contracts in app/schemas.py and app/analyzer/schemas.py. */

export type Sentiment = "positive" | "negative" | "neutral" | "doubt" | "spam";
export type Intent = "potential_buyer" | "hater" | "praise" | "question" | "other";
export type InsightType = "strength" | "improvement" | "trend";
export type VideoStatus = "processing" | "completed" | "failed";

export interface VideoInfo {
  instagram_id: string;
  url: string;
  media_type: string | null;
  /** The post belongs to a connected account: metrics come from the official API. */
  owned: boolean;
  creator_username: string | null;
  caption: string | null;
  thumbnail_url: string | null;
  published_at: string | null;
  duration_sec: number | null;
}

export type EngagementBasis = "reach" | "views" | "followers";

export interface Metrics {
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
  reach: number | null;
  engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
  engagement_score: number | null;
}

/** One point of a tracked link's metric history. */
export interface MetricPoint {
  collected_at: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
  reach: number | null;
  engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
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
  tracked: boolean;
  history: MetricPoint[];
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
  cached: boolean;
  data: AnalysisData;
}

export interface TrackingResponse {
  status: "success";
  video_id: string;
  tracked: boolean;
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
  detail?: string;
}

/* ============================ connected accounts ==========================
   Mirrors app/schemas.py: the Meta-side half of the product.
   ======================================================================== */

export interface Session {
  auth_required: boolean;
  authenticated: boolean;
}

export interface Health {
  status: string;
  auth_required: boolean;
  database: string;
  redis: string;
  version: string | null;
  environment: string | null;
  meta_configured: boolean | null;
  login_flow: string | null;
  graph_api_version: string | null;
  scheduler_running: boolean | null;
  connected_accounts: number | null;
  analyses: number | null;
  cache_hit_ratio: number | null;
  next_collection_at: string | null;
}

export interface Account {
  id: number;
  ig_user_id: string;
  username: string | null;
  name: string | null;
  account_type: string | null;
  profile_picture_url: string | null;
  followers_count: number | null;
  media_count: number | null;
  is_active: boolean;
  last_collected_at: string | null;
  token_expires_at: string | null;
  created_at: string;
}

export interface AccountSummary {
  account: Account;
  tracked_media: number;
  snapshots: number;
  total_likes: number;
  total_comments: number;
  total_saved: number;
  total_shares: number;
  total_reach: number;
  total_views: number;
  average_engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
  first_collected_at: string | null;
  last_collected_at: string | null;
}

export interface TimeseriesPoint {
  collected_at: string;
  likes: number;
  comments: number;
  saved: number;
  shares: number;
  reach: number;
  views: number;
  engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
  media_count: number;
}

export interface MediaBreakdown {
  media_product_type: string;
  media_count: number;
  average_engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
  total_reach: number;
  total_views: number;
}

export interface Snapshot {
  id: number;
  collected_at: string;
  likes: number | null;
  comments: number | null;
  saved: number | null;
  shares: number | null;
  reach: number | null;
  views: number | null;
  total_interactions: number | null;
  engagement_rate: number | null;
  engagement_basis: EngagementBasis | null;
}

export interface MediaItem {
  id: number;
  ig_media_id: string;
  media_type: string | null;
  media_product_type: string | null;
  caption: string | null;
  permalink: string | null;
  thumbnail_url: string | null;
  media_url: string | null;
  timestamp: string | null;
  last_snapshot_at: string | null;
  latest_engagement_rate: number | null;
  latest?: Snapshot | null;
}

export interface MediaPage {
  items: MediaItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface MediaDetail extends MediaItem {
  snapshots: Snapshot[];
}

export type MediaOrder = "timestamp" | "engagement_rate" | "collected_at";

export interface MediaQuery {
  limit: number;
  offset: number;
  orderBy: MediaOrder;
  direction: "asc" | "desc";
  mediaProductType?: string;
  search?: string;
}

export type RunStatus = "running" | "success" | "partial" | "failed";

export interface CollectionRun {
  id: number;
  account_id: number | null;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  trigger: string;
  media_seen: number;
  media_created: number;
  snapshots_created: number;
  api_calls: number;
  error: string | null;
}

export interface CollectionResult {
  run_id: number;
  account_id: number;
  status: RunStatus;
  media_seen: number;
  media_created: number;
  snapshots_created: number;
  api_calls: number;
  error?: string | null;
}

export interface TokenRefreshResult {
  account_id: number;
  username: string | null;
  refreshed: boolean;
  expires_at: string | null;
  error: string | null;
}
