import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { describe, expect, it, vi } from "vitest";

import { makeAnalysis } from "../test/fixtures";
import {
  analyzeVideo,
  AnalysisFailedError,
  ApiError,
  deleteAnalysis,
  getAnalysis,
  http,
  listRecent,
  pollAnalysis,
  PollTimeoutError,
  sleep,
  toApiError,
} from "./api";

function response<T>(data: T, status = 200, headers: Record<string, string> = {}): AxiosResponse<T> {
  return { data, status, statusText: "", headers, config: { headers: new AxiosHeaders() } };
}

function httpError(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
  return new AxiosError(
    "Request failed",
    "ERR_BAD_RESPONSE",
    undefined,
    undefined,
    response(data, status, headers),
  );
}

const noWait = () => Promise.resolve();

describe("requests", () => {
  it("posts the URL and returns the accepted job", async () => {
    const post = vi
      .spyOn(http, "post")
      .mockResolvedValue(response({ status: "processing", video_id: "v1", message: "ok" }, 202));
    await expect(analyzeVideo("https://www.instagram.com/reel/ABC123/")).resolves.toMatchObject({
      video_id: "v1",
    });
    expect(post).toHaveBeenCalledWith(
      "/api/v1/analyze",
      { url: "https://www.instagram.com/reel/ABC123/" },
      { signal: undefined },
    );
  });

  it("lists, fetches and deletes analyses", async () => {
    const get = vi
      .spyOn(http, "get")
      .mockResolvedValue(response({ status: "success", data: [], pagination: {} }));
    await listRecent(5, 10);
    expect(get).toHaveBeenCalledWith("/api/v1/recent", {
      params: { limit: 5, offset: 10 },
      signal: undefined,
    });
    await getAnalysis("a/b");
    expect(get).toHaveBeenLastCalledWith("/api/v1/analysis/a%2Fb", { signal: undefined });
    const del = vi.spyOn(http, "delete").mockResolvedValue(response({ status: "deleted" }));
    await deleteAnalysis("v1");
    expect(del).toHaveBeenCalledWith("/api/v1/analysis/v1");
  });

  it("maps the backend error envelope, including rate limits", async () => {
    vi.spyOn(http, "post").mockRejectedValue(
      httpError(
        429,
        { status: "error", error: { code: "rate_limit_exceeded", message: "Limite excedido" } },
        { "retry-after": "40" },
      ),
    );
    const error = await analyzeVideo("x").catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: "rate_limit_exceeded",
      status: 429,
      retryAfterSeconds: 40,
      message: "Limite excedido",
    });
  });
});

describe("toApiError", () => {
  it("keeps error ids from 500 responses", () => {
    const error = toApiError(
      httpError(500, {
        status: "error",
        error: { code: "internal_error", message: "Erro", error_id: "abc123" },
      }),
    );
    expect(error.errorId).toBe("abc123");
  });

  it("handles responses without the envelope, network errors, timeouts and unknown values", () => {
    expect(toApiError(httpError(502, "<html>")).code).toBe("http_error");
    expect(toApiError(new AxiosError("Network Error", "ERR_NETWORK")).code).toBe("network_error");
    expect(toApiError(new AxiosError("timeout", "ECONNABORTED")).code).toBe("timeout");
    expect(toApiError(new Error("boom")).message).toBe("boom");
    expect(toApiError("weird").code).toBe("unknown");
    const existing = new ApiError("x", { code: "y" });
    expect(toApiError(existing)).toBe(existing);
  });
});

describe("pollAnalysis", () => {
  const processing = (step: number) =>
    response({ status: "processing", video_id: "v1", message: "", step, total_steps: 8 });

  it("reports progress and resolves with the data", async () => {
    const data = makeAnalysis();
    vi.spyOn(http, "get")
      .mockResolvedValueOnce(processing(2))
      .mockResolvedValueOnce(processing(5))
      .mockResolvedValueOnce(response({ status: "success", data }));
    const onProgress = vi.fn();
    const sleeps: number[] = [];

    await expect(
      pollAnalysis("v1", { onProgress, sleep: (ms) => (sleeps.push(ms), Promise.resolve()) }),
    ).resolves.toEqual(data);
    expect(onProgress.mock.calls.map(([progress]) => progress.step)).toEqual([2, 5]);
    expect(sleeps).toEqual([2000, 2000]);
  });

  it("fails immediately when the analysis failed", async () => {
    vi.spyOn(http, "get").mockResolvedValue(
      response({ status: "error", video_id: "v1", message: "post privado" }),
    );
    const error = await pollAnalysis("v1", { sleep: noWait }).catch((reason) => reason);
    expect(error).toBeInstanceOf(AnalysisFailedError);
    expect(error.message).toBe("post privado");
  });

  it("gives up after the attempt ceiling (60 x 2 s by default)", async () => {
    const get = vi.spyOn(http, "get").mockResolvedValue(processing(3));
    const error = await pollAnalysis("v1", { maxAttempts: 4, sleep: noWait }).catch((reason) => reason);
    expect(error).toBeInstanceOf(PollTimeoutError);
    expect(error.videoId).toBe("v1");
    expect(get).toHaveBeenCalledTimes(4);
  });

  it("tolerates up to two consecutive network failures", async () => {
    const data = makeAnalysis();
    vi.spyOn(http, "get")
      .mockRejectedValueOnce(new AxiosError("Network Error", "ERR_NETWORK"))
      .mockRejectedValueOnce(new AxiosError("timeout", "ECONNABORTED"))
      .mockResolvedValueOnce(response({ status: "success", data }));
    await expect(pollAnalysis("v1", { sleep: noWait })).resolves.toEqual(data);
  });

  it("stops after three consecutive network failures or any HTTP error", async () => {
    vi.spyOn(http, "get").mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));
    await expect(pollAnalysis("v1", { sleep: noWait })).rejects.toMatchObject({ code: "network_error" });

    vi.spyOn(http, "get").mockRejectedValue(
      httpError(404, { status: "error", error: { code: "not_found", message: "Análise não encontrada" } }),
    );
    await expect(pollAnalysis("v1", { sleep: noWait })).rejects.toMatchObject({ code: "not_found" });
  });

  it("can be cancelled while waiting", async () => {
    vi.spyOn(http, "get").mockResolvedValue(processing(1));
    const controller = new AbortController();
    const promise = pollAnalysis("v1", { signal: controller.signal, intervalMs: 10_000 });
    await Promise.resolve();
    controller.abort();
    await expect(promise).rejects.toMatchObject({ code: "cancelled" });
  });
});

describe("sleep", () => {
  it("resolves after the delay and rejects when already aborted", async () => {
    vi.useFakeTimers();
    const done = vi.fn();
    const pending = sleep(1000).then(done);
    await vi.advanceTimersByTimeAsync(1000);
    await pending;
    expect(done).toHaveBeenCalled();
    vi.useRealTimers();

    const controller = new AbortController();
    controller.abort();
    await expect(sleep(10, controller.signal)).rejects.toMatchObject({ code: "cancelled" });
  });
});
