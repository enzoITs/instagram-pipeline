import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { makeAnalysis } from "./test/fixtures";
import * as accountsApi from "./utils/accountsApi";
import * as api from "./utils/api";

const REEL = "https://www.instagram.com/reel/C8xYz12AbCd/";

function mockRecent() {
  return vi.spyOn(api, "listRecent").mockResolvedValue({
    status: "success",
    data: [],
    pagination: { total: 0, limit: 8, offset: 0, page: 1 },
  });
}

/** Signed in, no accounts connected: the analysis tab is what these tests exercise. */
function mockSession(authRequired = false) {
  vi.spyOn(accountsApi, "readSession").mockResolvedValue({
    auth_required: authRequired,
    authenticated: true,
  });
  vi.spyOn(accountsApi, "readHealth").mockRejectedValue(new Error("no health in tests"));
  vi.spyOn(accountsApi, "listAccounts").mockResolvedValue([]);
}

async function openAnalysisTab() {
  await userEvent.click(await screen.findByRole("button", { name: "Análise por link" }));
}

async function submit(url = REEL) {
  await userEvent.type(screen.getByLabelText("Link do Reel ou post"), url);
  await userEvent.click(screen.getByRole("button", { name: "Analisar comentários" }));
}

describe("App flow", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    mockSession();
  });

  it("goes from form to loading to dashboard", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockResolvedValue({ status: "processing", video_id: "v1", message: "ok" });
    let finish!: (data: ReturnType<typeof makeAnalysis>) => void;
    const poll = vi.spyOn(api, "pollAnalysis").mockImplementation((_id, options) => {
      options?.onProgress?.(
        { status: "processing", video_id: "v1", message: "", step: 4, total_steps: 8 },
        1,
      );
      return new Promise((resolve) => {
        finish = resolve;
      });
    });

    render(<App />);
    await openAnalysisTab();
    await submit();

    expect(await screen.findByRole("heading", { name: "Lendo os comentários" })).toBeInTheDocument();
    expect(screen.getByText("Etapa 4 de 8: Analisando sentimento, intenção e tópicos")).toBeInTheDocument();
    expect(poll).toHaveBeenCalledWith("v1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(window.location.hash).toBe("#/analise/v1");

    finish(makeAnalysis());
    expect(
      await screen.findByRole("heading", { level: 1, name: /200 comentários analisados/ }),
    ).toBeInTheDocument();

    vi.spyOn(api, "deleteAnalysis").mockResolvedValue();
    await userEvent.click(screen.getByRole("button", { name: "Excluir análise" }));
    await userEvent.click(screen.getByRole("button", { name: "Excluir" }));
    expect(await screen.findByRole("heading", { name: /O que estão dizendo/ })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/analise");
  });

  it("shows cached results immediately", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockResolvedValue({
      status: "success",
      video_id: "v1",
      cached: true,
      data: makeAnalysis(),
    });
    const poll = vi.spyOn(api, "pollAnalysis");
    render(<App />);
    await openAnalysisTab();
    await submit();
    expect(await screen.findByText(/Resultado salvo de/)).toBeInTheDocument();
    expect(poll).not.toHaveBeenCalled();
  });

  it("follows and stops following a link", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockResolvedValue({
      status: "success",
      video_id: "v1",
      cached: true,
      data: makeAnalysis(),
    });
    const track = vi
      .spyOn(api, "setTracking")
      .mockResolvedValue({ status: "success", video_id: "v1", tracked: true });

    render(<App />);
    await openAnalysisTab();
    await submit();

    await userEvent.click(await screen.findByRole("button", { name: "Acompanhar este link" }));
    expect(track).toHaveBeenCalledWith("7b1e3c1e-1111-4f5e-9a1a-1234567890ab", true);
    expect(await screen.findByRole("button", { name: "Parar de acompanhar" })).toBeInTheDocument();
  });

  it("returns to the form with the failure reason and keeps the URL for a retry", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockResolvedValue({ status: "processing", video_id: "v1", message: "ok" });
    vi.spyOn(api, "pollAnalysis").mockRejectedValue(
      new api.AnalysisFailedError("Post privado ou exige login"),
    );
    render(<App />);
    await openAnalysisTab();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Post privado");
    expect(screen.getByLabelText("Link do Reel ou post")).toHaveValue(REEL);
  });

  it("explains rate limits and server errors from the submission", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockRejectedValue(
      new api.ApiError("Fila indisponível.", {
        code: "queue_unavailable",
        status: 503,
        retryAfterSeconds: 30,
        errorId: "abc",
      }),
    );
    render(<App />);
    await openAnalysisTab();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Fila indisponível. Código do erro: abc. Tente de novo em 30 s.",
    );
  });

  it("lets the user keep waiting after the polling ceiling", async () => {
    mockRecent();
    vi.spyOn(api, "analyzeVideo").mockResolvedValue({ status: "processing", video_id: "v9", message: "ok" });
    const poll = vi.spyOn(api, "pollAnalysis").mockRejectedValueOnce(new api.PollTimeoutError("v9"));
    render(<App />);
    await openAnalysisTab();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("demorando mais que o esperado");

    poll.mockImplementation(() => new Promise(() => {}));
    await userEvent.click(screen.getByRole("button", { name: "Continuar acompanhando" }));
    expect(await screen.findByRole("heading", { name: "Lendo os comentários" })).toBeInTheDocument();
    expect(poll).toHaveBeenLastCalledWith("v9", expect.anything());
  });

  it("resumes an analysis from the URL and opens failed recent items", async () => {
    window.history.replaceState(null, "", "/#/analise/from-url");
    vi.spyOn(api, "listRecent").mockResolvedValue({
      status: "success",
      pagination: { total: 1, limit: 8, offset: 0, page: 1 },
      data: [
        {
          id: "failed-1",
          instagram_id: "FAIL123",
          url: "https://www.instagram.com/p/FAIL123/",
          creator_username: null,
          status: "failed",
          created_at: new Date().toISOString(),
          thumbnail_url: null,
          engagement_score: null,
        },
      ],
    });
    const poll = vi
      .spyOn(api, "pollAnalysis")
      .mockRejectedValueOnce(new api.ApiError("Análise não encontrada", { code: "not_found", status: 404 }));
    vi.spyOn(api, "getAnalysis").mockResolvedValue({
      status: "error",
      video_id: "failed-1",
      message: "Instagram indisponível",
    });

    render(<App />);
    await waitFor(() => expect(poll).toHaveBeenCalledWith("from-url", expect.anything()));
    expect(await screen.findByRole("alert")).toHaveTextContent("Análise não encontrada");

    await userEvent.click(await screen.findByText("FAIL123"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Instagram indisponível"));
    expect(screen.getByLabelText("Link do Reel ou post")).toHaveValue("https://www.instagram.com/p/FAIL123/");
  });

  it("toggles the color theme", async () => {
    mockRecent();
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Usar tema escuro" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: "Usar tema claro" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("authentication", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("asks for the password and signs in", async () => {
    const session = vi
      .spyOn(accountsApi, "readSession")
      .mockResolvedValue({ auth_required: true, authenticated: false });
    vi.spyOn(accountsApi, "readHealth").mockRejectedValue(new Error("no health"));
    vi.spyOn(accountsApi, "listAccounts").mockResolvedValue([]);
    const signIn = vi
      .spyOn(accountsApi, "signIn")
      .mockResolvedValue({ auth_required: true, authenticated: true });

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Entrar no painel" })).toBeInTheDocument();

    session.mockResolvedValue({ auth_required: true, authenticated: true });
    await userEvent.type(screen.getByLabelText("Senha"), "segredo");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));

    expect(signIn).toHaveBeenCalledWith("segredo");
    expect(await screen.findByRole("button", { name: "Sair" })).toBeInTheDocument();
  });

  it("shows the reason a sign-in was refused", async () => {
    vi.spyOn(accountsApi, "readSession").mockResolvedValue({ auth_required: true, authenticated: false });
    vi.spyOn(accountsApi, "signIn").mockRejectedValue(
      new api.ApiError("Limite de 5 tentativas de login por minuto excedido.", {
        code: "rate_limit_exceeded",
        status: 429,
        retryAfterSeconds: 40,
      }),
    );

    render(<App />);
    await userEvent.type(await screen.findByLabelText("Senha"), "errada");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("5 tentativas de login");
    expect(screen.getByRole("alert")).toHaveTextContent("40 s");
  });
});
