import axios, { AxiosError } from "axios";

import type {
  AnalysisData,
  AnalysisProcessingResponse,
  AnalysisStatusResponse,
  AnalyzeResponse,
  ApiErrorBody,
  RecentResponse,
} from "../types";

export const POLL_INTERVAL_MS = 2000;
export const POLL_MAX_ATTEMPTS = 60; // 60 x 2 s = the 2-minute ceiling: never leave the user hanging
const MAX_CONSECUTIVE_NETWORK_ERRORS = 3;

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

export class ApiError extends Error {
  readonly code: string;
  readonly status?: number;
  readonly errorId?: string;
  readonly retryAfterSeconds?: number;

  constructor(
    message: string,
    options: { code: string; status?: number; errorId?: string; retryAfterSeconds?: number },
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code;
    this.status = options.status;
    this.errorId = options.errorId;
    this.retryAfterSeconds = options.retryAfterSeconds;
  }
}

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

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as ApiErrorBody).status === "error" &&
    typeof (value as ApiErrorBody).error?.message === "string"
  );
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (axios.isCancel(error)) return new ApiError("Operação cancelada.", { code: "cancelled" });
  if (error instanceof AxiosError) {
    const response = error.response;
    if (!response) {
      if (error.code === "ECONNABORTED") {
        return new ApiError("A API demorou para responder. Tente novamente.", { code: "timeout" });
      }
      return new ApiError("Não foi possível conectar à API. Verifique sua conexão e tente novamente.", {
        code: "network_error",
      });
    }
    const retryAfter = Number(response.headers?.["retry-after"]);
    const common = {
      status: response.status,
      retryAfterSeconds: Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
    };
    if (isErrorBody(response.data)) {
      return new ApiError(response.data.error.message, {
        ...common,
        code: response.data.error.code,
        errorId: response.data.error.error_id,
      });
    }
    return new ApiError(`Erro inesperado da API (HTTP ${response.status}).`, {
      ...common,
      code: "http_error",
    });
  }
  return new ApiError(error instanceof Error ? error.message : "Erro desconhecido.", { code: "unknown" });
}

async function request<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await fn()).data;
  } catch (error) {
    throw toApiError(error);
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
 * kill a two-minute wait); HTTP errors such as 404 fail immediately.
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
