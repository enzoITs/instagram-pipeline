import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { makeAnalysis, makeComments } from "../test/fixtures";
import * as api from "../utils/api";
import * as exporter from "../utils/export";
import AnalysisForm from "./AnalysisForm";
import Dashboard from "./Dashboard";
import ErrorBoundary from "./ErrorBoundary";
import LoadingSpinner from "./LoadingSpinner";
import RecentAnalyses from "./RecentAnalyses";

describe("AnalysisForm", () => {
  it("validates the URL before submitting", async () => {
    const onSubmit = vi.fn();
    render(<AnalysisForm onSubmit={onSubmit} />);
    const input = screen.getByLabelText("Link do Reel ou post");

    await userEvent.click(screen.getByRole("button", { name: "Analisar comentários" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Cole o link");
    expect(input).toHaveAttribute("aria-invalid", "true");

    await userEvent.type(input, "https://youtube.com/watch?v=1");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // typing clears the error
    await userEvent.click(screen.getByRole("button", { name: "Analisar comentários" }));
    expect(screen.getByRole("alert")).toHaveTextContent("instagram.com");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits the canonical URL", async () => {
    const onSubmit = vi.fn();
    render(<AnalysisForm onSubmit={onSubmit} />);
    await userEvent.type(
      screen.getByLabelText("Link do Reel ou post"),
      "instagram.com/reels/ABC123?igsh=x{enter}",
    );
    expect(onSubmit).toHaveBeenCalledWith("https://www.instagram.com/reel/ABC123/");
    expect(screen.getByLabelText("Link do Reel ou post")).toHaveValue(
      "https://www.instagram.com/reel/ABC123/",
    );
  });

  it("shows server errors and a busy state", () => {
    render(
      <AnalysisForm
        onSubmit={vi.fn()}
        busy
        error="Limite excedido"
        initialUrl="https://www.instagram.com/p/ABC123/"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Limite excedido");
    expect(screen.getByRole("button", { name: "Enviando…" })).toBeDisabled();
    expect(screen.getByLabelText("Link do Reel ou post")).toBeDisabled();
  });
});

describe("LoadingSpinner", () => {
  it("shows step progress, elapsed time and lets the user leave", async () => {
    vi.useFakeTimers();
    const onCancel = vi.fn();
    const startedAt = Date.now();
    render(
      <LoadingSpinner
        step={3}
        target="https://www.instagram.com/reel/ABC123/"
        startedAt={startedAt}
        onCancel={onCancel}
      />,
    );

    const items = within(screen.getByRole("list", { name: "Etapas da análise" })).getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(items[0]).toHaveClass("steps__item--done");
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(items[3]).toHaveClass("steps__item--pending");
    expect(screen.getByText("Etapa 3 de 8: Salvando métricas e comentários")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByRole("timer")).toHaveTextContent("1:05");
    vi.useRealTimers();

    await userEvent.click(screen.getByRole("button", { name: "Voltar ao início" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("describes the queued state", () => {
    render(<LoadingSpinner step={0} startedAt={Date.now()} onCancel={vi.fn()} />);
    expect(screen.getByText("Etapa 0 de 8: Na fila de processamento")).toBeInTheDocument();
  });
});

describe("ErrorBoundary", () => {
  function Broken(): never {
    throw new Error("widget exploded");
  }

  it("renders a recovery screen instead of a blank page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onReset = vi.fn();
    const { rerender } = render(
      <ErrorBoundary onReset={onReset}>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("widget exploded");

    rerender(
      <ErrorBoundary onReset={onReset}>
        <p>ok again</p>
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Voltar ao início" }));
    expect(onReset).toHaveBeenCalled();
    expect(screen.getByText("ok again")).toBeInTheDocument();
  });
});

describe("RecentAnalyses", () => {
  it("lists recent analyses with status labels and opens one", async () => {
    vi.spyOn(api, "listRecent").mockResolvedValue({
      status: "success",
      pagination: { total: 2, limit: 8, offset: 0, page: 1 },
      data: [
        {
          id: "1",
          instagram_id: "ABC123",
          url: "https://www.instagram.com/reel/ABC123/",
          creator_username: "@brand",
          status: "completed",
          created_at: new Date().toISOString(),
          thumbnail_url: null,
          engagement_score: 8.9,
        },
        {
          id: "2",
          instagram_id: "DEF456",
          url: null,
          creator_username: null,
          status: "failed",
          created_at: new Date().toISOString(),
          thumbnail_url: null,
          engagement_score: null,
        },
      ],
    });
    const onOpen = vi.fn();
    render(<RecentAnalyses refreshKey={0} onOpen={onOpen} />);

    expect(await screen.findByText("@brand")).toBeInTheDocument();
    expect(screen.getByText("Concluída")).toBeInTheDocument();
    expect(screen.getByText("Falhou")).toBeInTheDocument();
    expect(screen.getByText("Score 8,9")).toBeInTheDocument();
    await userEvent.click(screen.getByText("DEF456"));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "2" }));
  });

  it("shows empty and error states", async () => {
    vi.spyOn(api, "listRecent").mockResolvedValueOnce({
      status: "success",
      data: [],
      pagination: { total: 0, limit: 8, offset: 0, page: 1 },
    });
    const { rerender } = render(<RecentAnalyses refreshKey={0} onOpen={vi.fn()} />);
    expect(await screen.findByText(/Nenhuma análise ainda/)).toBeInTheDocument();

    vi.spyOn(api, "listRecent").mockRejectedValueOnce(
      new api.ApiError("API fora do ar", { code: "network_error" }),
    );
    rerender(<RecentAnalyses refreshKey={1} onOpen={vi.fn()} />);
    expect(await screen.findByText(/API fora do ar/)).toBeInTheDocument();
  });
});

describe("Dashboard", () => {
  const renderDashboard = (overrides = {}, props = {}) => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    const onNewAnalysis = vi.fn();
    const onToggleTracking = vi.fn();
    render(
      <Dashboard
        data={makeAnalysis(overrides)}
        onDelete={onDelete}
        onNewAnalysis={onNewAnalysis}
        onToggleTracking={onToggleTracking}
        {...props}
      />,
    );
    return { onDelete, onNewAnalysis, onToggleTracking };
  };

  it("renders the summary, metrics, charts, insights and topics", () => {
    renderDashboard({}, { cached: true });
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("200 comentários analisados: 25 querem comprar");
    expect(heading).toHaveFocus();
    expect(screen.getByText("Resultado salvo de", { exact: false })).toBeInTheDocument();

    const metrics = screen.getByRole("region", { name: "Métricas de engajamento" });
    expect(within(metrics).getByText("250 mil")).toBeInTheDocument();
    expect(within(metrics).getByText("7,89%")).toBeInTheDocument();
    expect(within(metrics).getByRole("meter", { name: "Score de engajamento" })).toHaveAttribute(
      "aria-valuenow",
      "5.8",
    );
    expect(within(metrics).getByText(/só são visíveis para o dono do post/)).toBeInTheDocument();
    expect(within(metrics).getByText(/Sobre as visualizações/)).toBeInTheDocument();

    const sentiment = screen.getByRole("region", { name: "Sentimento" });
    expect(within(sentiment).getByText("60,0%")).toBeInTheDocument();
    expect(within(sentiment).getByText("120 comentários")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Alto potencial de conversão" })).toBeInTheDocument();
    expect(screen.getByText("Impacto 8,4 de 10")).toBeInTheDocument();
    expect(screen.getByText("produto incrível")).toBeInTheDocument();
    expect(screen.getByText("negativo")).toBeInTheDocument();
    expect(screen.getByText(/coletados via Apify/)).toBeInTheDocument();
    expect(screen.getByText("Avisos do processamento (1)")).toBeInTheDocument();
    expect(screen.queryByText(/Dados de demonstração/)).not.toBeInTheDocument();
  });

  it("offers to follow the link over time", async () => {
    const { onToggleTracking } = renderDashboard();
    const history = screen.getByRole("region", { name: "Evolução deste link" });
    expect(within(history).getByText(/Só há a medição desta análise/)).toBeInTheDocument();
    await userEvent.click(within(history).getByRole("button", { name: "Acompanhar este link" }));
    expect(onToggleTracking).toHaveBeenCalledWith(true);
  });

  it("offers table views for the charts", async () => {
    renderDashboard();
    const intent = screen.getByRole("region", { name: "Intenção" });
    await userEvent.click(within(intent).getByRole("button", { name: "Ver tabela" }));
    const table = within(intent).getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "Elogio" })).toBeInTheDocument();
    expect(within(table).getByText("110")).toBeInTheDocument();
    await userEvent.click(within(intent).getByRole("button", { name: "Ver gráfico" }));
    expect(within(intent).queryByRole("table")).not.toBeInTheDocument();

    const sentiment = screen.getByRole("region", { name: "Sentimento" });
    await userEvent.click(within(sentiment).getByRole("button", { name: "Ver tabela" }));
    expect(within(sentiment).getByRole("rowheader", { name: "Dúvida" })).toBeInTheDocument();
  });

  it("filters, paginates and highlights comments", async () => {
    renderDashboard();
    const triage = screen.getByRole("region", { name: "Comentários para triagem" });
    expect(within(triage).getAllByRole("listitem")).toHaveLength(10);
    expect(within(triage).getByText("Página 1 de 3")).toBeInTheDocument();
    const marks = triage.querySelectorAll("mark");
    expect([...marks].slice(0, 3).map((mark) => mark.textContent)).toEqual(["vende", "Produto", "INCRÍVEL"]);

    await userEvent.click(within(triage).getByRole("button", { name: "Próximos" }));
    expect(within(triage).getByText("Página 2 de 3")).toBeInTheDocument();
    expect(within(triage).getByRole("button", { name: "Anteriores" })).toBeEnabled();

    await userEvent.click(within(triage).getByRole("radio", { name: /Quer comprar/ }));
    expect(within(triage).getAllByRole("listitem")).toHaveLength(5);
    expect(within(triage).queryByRole("navigation")).not.toBeInTheDocument();

    await userEvent.selectOptions(within(triage).getByRole("combobox", { name: "Sentimento" }), "neutral");
    expect(within(triage).getByText(/Nenhum comentário da amostra/)).toBeInTheDocument();
    await userEvent.click(within(triage).getByRole("button", { name: "Limpar filtros" }));
    expect(within(triage).getAllByRole("listitem")).toHaveLength(10);
  });

  it("exports the report as JSON", async () => {
    const exportSpy = vi.spyOn(exporter, "exportToJSON").mockImplementation(() => {});
    renderDashboard();
    await userEvent.click(screen.getByRole("button", { name: "Baixar relatório JSON" }));
    expect(exportSpy).toHaveBeenCalledWith(
      expect.objectContaining({ video_id: "7b1e3c1e-1111-4f5e-9a1a-1234567890ab" }),
    );
  });

  it("confirms before deleting and reports failures", async () => {
    const { onDelete, onNewAnalysis } = renderDashboard();
    await userEvent.click(screen.getByRole("button", { name: "Excluir análise" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onDelete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Excluir análise" }));
    onDelete.mockRejectedValueOnce(new Error("Falha ao excluir"));
    await userEvent.click(screen.getByRole("button", { name: "Excluir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Falha ao excluir");

    await userEvent.click(screen.getByRole("button", { name: "Excluir" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledTimes(2));

    await userEvent.click(screen.getByRole("button", { name: "Nova análise" }));
    expect(onNewAnalysis).toHaveBeenCalled();
  });

  it("flags demo data and handles missing values", () => {
    const base = makeAnalysis();
    renderDashboard({
      metrics: {
        ...base.metrics,
        views: null,
        engagement_rate: null,
        engagement_basis: null,
        engagement_score: null,
        shares: 10,
        saves: 3,
      },
      sentiment: { positive: 0, negative: 0, neutral: 0, doubt: 0, spam: 0 },
      intents: { potential_buyer: 0, hater: 0, praise: 0, question: 0, other: 0 },
      top_topics: [],
      sentiment_comments_sample: makeComments(0),
      video: {
        ...base.video,
        thumbnail_url: "https://cdn.example/thumb.jpg",
        creator_username: null,
        caption: null,
      },
      processing: {
        ...base.processing,
        data_source: "mock",
        nlp_backend: "lexicon",
        insights_source: "rules",
        comments_analyzed: 0,
        warnings: ["Dados de demonstração gerados localmente (SCRAPER_MODE=mock); não refletem o post real."],
      },
    });
    expect(screen.getByText(/Dados de demonstração/)).toBeInTheDocument();
    expect(screen.queryByText(/Avisos do processamento/)).not.toBeInTheDocument();
    expect(screen.getByText("Compartilhamentos")).toBeInTheDocument();
    expect(screen.getByText(/Sem denominador confiável/)).toBeInTheDocument();
    expect(screen.getAllByText("Sem comentários analisados.")).toHaveLength(2);
    expect(screen.getByText(/Poucos comentários/)).toBeInTheDocument();
    expect(screen.getByText("Criador não identificado")).toBeInTheDocument();
    expect(screen.getByText(/regras de análise/)).toBeInTheDocument();
    expect(screen.getByText(/léxico de sentimento/)).toBeInTheDocument();
    const image = screen.getByRole("img", { name: /Capa do reel/ });
    image.dispatchEvent(new Event("error"));
  });
});
