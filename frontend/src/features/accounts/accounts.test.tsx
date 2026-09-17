import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../App";
import { makeAccount, makeAnalysis, makeMedia, makeSummary, makeTimeseries } from "../../test/fixtures";
import * as accountsApi from "../../utils/accountsApi";
import * as api from "../../utils/api";

function mockAccountsTab(overrides: { accounts?: ReturnType<typeof makeAccount>[] } = {}) {
  vi.spyOn(accountsApi, "readSession").mockResolvedValue({ auth_required: false, authenticated: true });
  vi.spyOn(accountsApi, "readHealth").mockResolvedValue({
    status: "ok",
    auth_required: false,
    database: "ok",
    redis: "ok",
    version: "2.0.0",
    environment: "test",
    meta_configured: true,
    login_flow: "instagram_login",
    graph_api_version: "v23.0",
    scheduler_running: true,
    connected_accounts: 1,
    analyses: 3,
    cache_hit_ratio: null,
    next_collection_at: null,
  });
  vi.spyOn(accountsApi, "listAccounts").mockResolvedValue(overrides.accounts ?? [makeAccount()]);
  vi.spyOn(accountsApi, "readSummary").mockResolvedValue(makeSummary());
  vi.spyOn(accountsApi, "readTimeseries").mockResolvedValue(makeTimeseries());
  vi.spyOn(accountsApi, "readBreakdown").mockResolvedValue([
    {
      media_product_type: "REELS",
      media_count: 12,
      average_engagement_rate: 13.5,
      engagement_basis: "reach",
      total_reach: 24000,
      total_views: 61000,
    },
  ]);
  vi.spyOn(accountsApi, "listRuns").mockResolvedValue([
    {
      id: 3,
      account_id: 1,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      status: "success",
      trigger: "scheduled",
      media_seen: 24,
      media_created: 0,
      snapshots_created: 24,
      api_calls: 26,
      error: null,
    },
  ]);
  vi.spyOn(accountsApi, "listMedia").mockResolvedValue({
    items: [makeMedia()],
    total: 1,
    limit: 25,
    offset: 0,
  });
  vi.spyOn(api, "listRecent").mockResolvedValue({
    status: "success",
    data: [],
    pagination: { total: 0, limit: 8, offset: 0, page: 1 },
  });
}

describe("Accounts tab", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/#/contas");
  });

  it("shows the account summary, the post table and the run history", async () => {
    mockAccountsTab();
    render(<App />);

    expect(await screen.findByText("Posts monitorados")).toBeInTheDocument();
    expect(screen.getByText("13,12%")).toBeInTheDocument();
    expect(screen.getByText("Média ponderada sobre o alcance")).toBeInTheDocument();

    const table = screen.getByRole("region", { name: "Posts coletados" });
    expect(within(table).getByText("Reels sobre métricas")).toBeInTheDocument();
    expect(within(table).getByText("13,50%")).toBeInTheDocument();

    const runs = screen.getByRole("region", { name: "Últimas coletas" });
    expect(within(runs).getByText("concluída")).toBeInTheDocument();
  });

  it("invites the user to connect an account when there is none", async () => {
    mockAccountsTab({ accounts: [] });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Nenhuma conta conectada" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Conectar conta/ })).toHaveAttribute("href", "/auth/login");
  });

  it("collects on demand and reports the result", async () => {
    mockAccountsTab();
    vi.spyOn(accountsApi, "startCollection").mockResolvedValue({
      run_id: 9,
      account_id: 1,
      status: "running",
      media_seen: 0,
      media_created: 0,
      snapshots_created: 0,
      api_calls: 0,
    });
    vi.spyOn(accountsApi, "followRun").mockResolvedValue({
      id: 9,
      account_id: 1,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      status: "success",
      trigger: "manual",
      media_seen: 24,
      media_created: 1,
      snapshots_created: 24,
      api_calls: 26,
      error: null,
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Coletar agora" }));
    expect(await screen.findByText(/24 posts atualizados em 26 chamadas/)).toBeInTheDocument();
  });

  it("sends a post of the account to the link analysis", async () => {
    mockAccountsTab();
    const analyze = vi.spyOn(api, "analyzeVideo").mockResolvedValue({
      status: "success",
      video_id: "v1",
      cached: true,
      data: makeAnalysis({
        video: { ...makeAnalysis().video, owned: true },
        metrics: { ...makeAnalysis().metrics, reach: 2000, engagement_basis: "reach" },
      }),
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Analisar comentários" }));

    await waitFor(() =>
      expect(analyze).toHaveBeenCalledWith("https://www.instagram.com/reel/C8xYz12AbCd/"),
    );
    expect(await screen.findByText("Métricas oficiais da conta conectada")).toBeInTheDocument();
    expect(screen.getByText("Alcance")).toBeInTheDocument();
  });
});
