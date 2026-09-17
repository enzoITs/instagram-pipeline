import { useEffect, useRef, useState } from "react";

import "../styles/Dashboard.css";
import type { AnalysisData } from "../types";
import { exportToJSON } from "../utils/export";
import { formatRelative, plural } from "../utils/format";
import { summarize } from "../utils/text";
import CommentTriage from "./dashboard/CommentTriage";
import HistoryChart from "./dashboard/HistoryChart";
import InsightList from "./dashboard/InsightList";
import IntentChart from "./dashboard/IntentChart";
import MetricsRow from "./dashboard/MetricsRow";
import SentimentChart from "./dashboard/SentimentChart";
import TopicList from "./dashboard/TopicList";
import VideoHeader from "./dashboard/VideoHeader";
import { AlertIcon, BackIcon, DownloadIcon, TrashIcon } from "./Icons";

interface Props {
  data: AnalysisData;
  cached?: boolean;
  trackingBusy?: boolean;
  onNewAnalysis: () => void;
  onDelete: () => Promise<void>;
  onToggleTracking?: (tracked: boolean) => void;
}

const SOURCE_LABELS: Record<string, string> = {
  apify: "Apify",
  instaloader: "Instaloader",
  mock: "dados de demonstração",
  graph_api: "API oficial da Meta",
  "graph_api+apify": "API oficial da Meta (comentários via Apify)",
  "graph_api+instaloader": "API oficial da Meta (comentários via Instaloader)",
  "graph_api+mock": "API oficial da Meta (comentários simulados)",
};

export default function Dashboard({
  data,
  cached = false,
  trackingBusy = false,
  onNewAnalysis,
  onDelete,
  onToggleTracking,
}: Props) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { processing } = data;
  const isDemo = processing.data_source === "mock";
  const otherWarnings = processing.warnings.filter(
    (warning) => !(isDemo && warning.includes("SCRAPER_MODE=mock")),
  );

  useEffect(() => {
    headingRef.current?.focus();
  }, [data.video_id]);

  const handleDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete();
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Não foi possível excluir a análise.");
      setDeleting(false);
    }
  };

  return (
    <article className="dashboard" aria-labelledby="dashboard-title">
      <div className="toolbar">
        <button type="button" className="button button--ghost" onClick={onNewAnalysis}>
          <BackIcon size={16} />
          Nova análise
        </button>
        <div className="toolbar__actions">
          <button type="button" className="button button--primary" onClick={() => exportToJSON(data)}>
            <DownloadIcon size={16} />
            Baixar relatório JSON
          </button>
          {confirmingDelete ? (
            <span className="confirm" role="group" aria-label="Confirmar exclusão">
              <span className="confirm__text">Excluir esta análise de vez?</span>
              <button
                type="button"
                className="button button--danger"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? "Excluindo…" : "Excluir"}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setConfirmingDelete(false)}
                disabled={deleting}
              >
                Cancelar
              </button>
            </span>
          ) : (
            <button type="button" className="button button--ghost" onClick={() => setConfirmingDelete(true)}>
              <TrashIcon size={16} />
              Excluir análise
            </button>
          )}
        </div>
      </div>
      {deleteError && (
        <p className="notice notice--critical" role="alert">
          <AlertIcon size={18} />
          {deleteError}
        </p>
      )}

      {isDemo && (
        <p className="notice notice--demo">
          <AlertIcon size={18} />
          Dados de demonstração: o coletor está em modo simulado, então métricas e comentários não são do post
          real.
        </p>
      )}

      <header className="dashboard__intro">
        <VideoHeader video={data.video} />
        <div className="dashboard__summary">
          <h1 id="dashboard-title" ref={headingRef} tabIndex={-1} className="summary">
            {summarize(data)}
          </h1>
          {cached && processing.completed_at && (
            <p className="dashboard__cached">Resultado salvo de {formatRelative(processing.completed_at)}.</p>
          )}
        </div>
      </header>

      <MetricsRow metrics={data.metrics} owned={data.video.owned} />

      {onToggleTracking && (
        <HistoryChart
          history={data.history}
          tracked={data.tracked}
          busy={trackingBusy}
          onToggleTracking={onToggleTracking}
        />
      )}

      <div className="dashboard__grid">
        <SentimentChart distribution={data.sentiment} />
        <IntentChart distribution={data.intents} />
        <InsightList insights={data.insights} source={processing.insights_source} />
        <TopicList topics={data.top_topics} />
        <CommentTriage comments={data.sentiment_comments_sample} />
      </div>

      <footer className="dashboard__footer">
        <p>
          {plural(processing.comments_analyzed, "comentário coletado", "comentários coletados")} via{" "}
          {SOURCE_LABELS[processing.data_source ?? ""] ?? "fonte desconhecida"}, classificados com{" "}
          {processing.nlp_backend === "transformers"
            ? "modelos de linguagem multilíngues"
            : "léxico de sentimento"}
          {processing.duration_seconds !== null &&
            ` em ${Math.max(1, Math.round(processing.duration_seconds))} s`}
          .
        </p>
        {otherWarnings.length > 0 && (
          <details className="warnings">
            <summary>Avisos do processamento ({otherWarnings.length})</summary>
            <ul>
              {otherWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </details>
        )}
      </footer>
    </article>
  );
}
