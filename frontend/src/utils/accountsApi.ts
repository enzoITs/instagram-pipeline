import type {
  Account,
  AccountSummary,
  CollectionResult,
  CollectionRun,
  Health,
  MediaBreakdown,
  MediaDetail,
  MediaPage,
  MediaQuery,
  Session,
  TimeseriesPoint,
  TokenRefreshResult,
} from "../types";
import { http, request } from "./http";

// --------------------------------------------------------------------- session
export function readSession(signal?: AbortSignal): Promise<Session> {
  return request(() => http.get<Session>("/auth/session", { signal }));
}

export function signIn(password: string): Promise<Session> {
  return request(() => http.post<Session>("/auth/session", { password }));
}

export function signOut(): Promise<Session> {
  return request(() => http.delete<Session>("/auth/session"));
}

export function readHealth(signal?: AbortSignal): Promise<Health> {
  return request(() => http.get<Health>("/api/health", { signal }));
}

/** Full-page navigation: Meta's consent screen cannot be loaded over fetch. */
export function connectAccountUrl(): string {
  return "/auth/login";
}

export function disconnectAccount(accountId: number): Promise<Account> {
  return request(() => http.delete<Account>(`/auth/accounts/${accountId}`));
}

// -------------------------------------------------------------------- accounts
export function listAccounts(signal?: AbortSignal): Promise<Account[]> {
  return request(() => http.get<Account[]>("/api/accounts", { signal }));
}

export function readSummary(accountId: number, signal?: AbortSignal): Promise<AccountSummary> {
  return request(() => http.get<AccountSummary>(`/api/accounts/${accountId}/summary`, { signal }));
}

export function readTimeseries(
  accountId: number,
  days: number,
  signal?: AbortSignal,
): Promise<TimeseriesPoint[]> {
  return request(() =>
    http.get<TimeseriesPoint[]>(`/api/accounts/${accountId}/timeseries`, { params: { days }, signal }),
  );
}

export function readBreakdown(accountId: number, signal?: AbortSignal): Promise<MediaBreakdown[]> {
  return request(() => http.get<MediaBreakdown[]>(`/api/accounts/${accountId}/breakdown`, { signal }));
}

export function listMedia(accountId: number, query: MediaQuery, signal?: AbortSignal): Promise<MediaPage> {
  return request(() =>
    http.get<MediaPage>(`/api/accounts/${accountId}/media`, {
      params: {
        limit: query.limit,
        offset: query.offset,
        order_by: query.orderBy,
        direction: query.direction,
        media_product_type: query.mediaProductType || undefined,
        search: query.search || undefined,
      },
      signal,
    }),
  );
}

export function readMedia(mediaId: number, signal?: AbortSignal): Promise<MediaDetail> {
  return request(() => http.get<MediaDetail>(`/api/media/${mediaId}`, { signal }));
}

export function exportCsvUrl(accountId: number): string {
  return `/api/accounts/${accountId}/export.csv`;
}

// ------------------------------------------------------------------------ jobs
export function startCollection(accountId: number, limit?: number): Promise<CollectionResult> {
  return request(() =>
    http.post<CollectionResult>(`/api/accounts/${accountId}/collect`, null, {
      params: limit ? { limit } : undefined,
    }),
  );
}

export function readRun(runId: number, signal?: AbortSignal): Promise<CollectionRun> {
  return request(() => http.get<CollectionRun>(`/api/runs/${runId}`, { signal }));
}

export function listRuns(accountId: number, limit = 10, signal?: AbortSignal): Promise<CollectionRun[]> {
  return request(() =>
    http.get<CollectionRun[]>("/api/runs", { params: { account_id: accountId, limit }, signal }),
  );
}

export function refreshToken(accountId: number): Promise<TokenRefreshResult> {
  return request(() => http.post<TokenRefreshResult>(`/api/accounts/${accountId}/refresh-token`));
}

export interface FollowRunOptions {
  signal?: AbortSignal;
  onProgress?: (run: CollectionRun) => void;
  sleep?: (ms: number) => Promise<void>;
  timeoutMs?: number;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Follows a collection until it stops running.
 *
 * A full run is one Graph API call per post and can pause for minutes when Meta
 * reports the quota is nearly spent, so the browser polls the run row instead of
 * holding a request open — a proxy would give up long before a large account is done.
 */
export async function followRun(runId: number, options: FollowRunOptions = {}): Promise<CollectionRun> {
  const { signal, onProgress, sleep: pause = wait, timeoutMs = 30 * 60 * 1000 } = options;
  const startedAt = Date.now();
  let delay = 1000;

  for (;;) {
    const run = await readRun(runId, signal);
    if (run.status !== "running") return run;
    onProgress?.(run);
    if (Date.now() - startedAt > timeoutMs) {
      return run; // still going server-side; the caller says so and stops watching
    }
    await pause(delay);
    delay = Math.min(delay * 1.5, 5000);
  }
}
