import axios, { AxiosError } from "axios";

import type { ApiErrorBody } from "../types";

/** One client for both halves of the API; the session cookie rides along. */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 15_000,
  withCredentials: true,
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

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/**
 * Called whenever the API answers 401, so the shell can swap to the login screen
 * instead of leaving half-loaded panels behind an expired session.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
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
    // Older clients and FastAPI's own errors carry the message under `detail`.
    const detail = (response.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") {
      return new ApiError(detail, { ...common, code: `http_${response.status}` });
    }
    return new ApiError(`Erro inesperado da API (HTTP ${response.status}).`, {
      ...common,
      code: "http_error",
    });
  }
  return new ApiError(error instanceof Error ? error.message : "Erro desconhecido.", { code: "unknown" });
}

export async function request<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await fn()).data;
  } catch (error) {
    const apiError = toApiError(error);
    if (apiError.status === 401) onUnauthorized?.();
    throw apiError;
  }
}
