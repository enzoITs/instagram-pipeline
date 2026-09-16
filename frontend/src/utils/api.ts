import type {
  AnalysisData,
  AnalysisProcessingResponse,
  AnalysisStatusResponse,
  AnalyzeResponse,
  RecentResponse,
  TrackingResponse,
} from "../types";
import { ApiError, http, request, toApiError } from "./http";

export { ApiError, http, toApiError };

// The backend gives up on an analysis after ANALYSIS_STALE_AFTER_SECONDS (10 min
// by default) and reports it as failed, so the browser watches for just as long
// instead of claiming a timeout while the job is still running.
export const POLL_INTERVAL_MS = 2000;
export const POLL_MAX_ATTEMPTS = 150; // 150 x 2 s = 5 min
const MAX_CONSECUTIVE_NETWORK_ERRORS = 3;

/** The backend finished the job with status "error" (e.g. private post). */
export class AnalysisFailedError extends ApiError {
  constructor(message: string) {
    super(message, { code: "analysis_failed" });
    this.name = "AnalysisFailedError";
  }
}

/** Polling hit its ceiling; the job keeps running server-side. */
export class PollTimeoutError extends ApiError {
  readonly videoId: string;

  constructor(videoId: string) {
    super(
      "A análise está demorando mais que o esperado. Ela continua em segundo plano e aparece em Análises recentes quando terminar.",
      { code: "poll_timeout" },
    );
    this.name = "PollTimeoutError";
    this.videoId = videoId;
  }
}

export function analyzeVideo(url: string, signal?: AbortSignal): Promise<AnalyzeResponse> {
  return request(() => http.post<AnalyzeResponse>("/api/v1/analyze", { url }, { signal }));
}

export function getAnalysis(videoId: string, signal?: AbortSignal): Promise<AnalysisStatusResponse> {
  return request(() =>
    http.get<AnalysisStatusResponse>(`/api/v1/analysis/${encodeURIComponent(videoId)}`, { signal }),
  );
}

export function listRecent(limit = 10, offset = 0, signal?: AbortSignal): Promise<RecentResponse> {
  return request(() => http.get<RecentResponse>("/api/v1/recent", { params: { limit, offset }, signal }));
}

export async function deleteAnalysis(videoId: string): Promise<void> {
  await request(() => http.delete(`/api/v1/analysis/${encodeURIComponent(videoId)}`));
}

/** Tracked links get a fresh metrics snapshot on the schedule, building a history. */
export function setTracking(videoId: string, tracked: boolean): Promise<TrackingResponse> {
  return request(() =>
    http.put<TrackingResponse>(`/api/v1/analysis/${encodeURIComponent(videoId)}/tracking`, { tracked }),
  );
}

export interface PollOptions {
  intervalMs?: number;
  maxAttempts?: number;
  signal?: AbortSignal;
  onProgress?: (progress: AnalysisProcessingResponse, attempt: number) => void;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError("Operação cancelada.", { code: "cancelled" }));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new ApiError("Operação cancelada.", { code: "cancelled" }));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Polls GET /analysis/{id} every `intervalMs` until success, error or `maxAttempts`.
 * Up to three consecutive network failures are tolerated (flaky Wi-Fi should not
 * kill a long wait); HTTP errors such as 404 fail immediately.
 */
export async function pollAnalysis(videoId: string, options: PollOptions = {}): Promise<AnalysisData> {
  const {
    intervalMs = POLL_INTERVAL_MS,
    maxAttempts = POLL_MAX_ATTEMPTS,
    signal,
    onProgress,
    sleep: wait = sleep,
  } = options;
  let networkFailures = 0;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await getAnalysis(videoId, signal);
      networkFailures = 0;
      if (response.status === "success") return response.data;
      if (response.status === "error") throw new AnalysisFailedError(response.message);
      onProgress?.(response, attempt);
    } catch (error) {
      const apiError = toApiError(error);
      const transient = apiError.code === "network_error" || apiError.code === "timeout";
      if (!transient || ++networkFailures >= MAX_CONSECUTIVE_NETWORK_ERRORS) throw apiError;
    }
    if (attempt < maxAttempts) await wait(intervalMs, signal);
  }
  throw new PollTimeoutError(videoId);
}
