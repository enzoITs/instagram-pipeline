import { useMemo, useState } from "react";

import type { CommentSample, Intent, Sentiment } from "../../types";
import {
  formatInteger,
  formatPercent,
  formatRelative,
  INTENT_LABELS,
  INTENT_ORDER,
  SENTIMENT_LABELS,
  SENTIMENT_ORDER,
} from "../../utils/format";
import { highlightKeyPhrases } from "../../utils/text";
import { sentimentColor, useChartColors } from "../../utils/theme";
import { HeartIcon } from "../Icons";
import Panel from "./Panel";

const PAGE_SIZE = 10;

type IntentFilter = Intent | "all";
type SentimentFilter = Sentiment | "all";

function CommentText({ text, phrases }: { text: string; phrases: string[] }) {
  return (
    <p className="comment__text">
      {highlightKeyPhrases(text, phrases).map((part, index) =>
        part.marked ? (
          <mark key={index} className="marker">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </p>
  );
}

export default function CommentTriage({ comments }: { comments: CommentSample[] }) {
  const colors = useChartColors();
  const [intent, setIntent] = useState<IntentFilter>("all");
  const [sentiment, setSentiment] = useState<SentimentFilter>("all");
  const [page, setPage] = useState(0);

  // Counted over the comments the sentiment filter already allows, and only
  // where the intent is known — otherwise a chip would promise rows that the
  // filter below then drops.
  const counts = useMemo(() => {
    const visible = comments.filter(
      (comment) => sentiment === "all" || comment.sentiment_analysis?.sentiment === sentiment,
    );
    const byIntent: Record<string, number> = { all: visible.length };
    for (const comment of visible) {
      const key = comment.sentiment_analysis?.intent_category;
      if (!key) continue;
      byIntent[key] = (byIntent[key] ?? 0) + 1;
    }
    return byIntent;
  }, [comments, sentiment]);

  const filtered = useMemo(
    () =>
      comments.filter((comment) => {
        const analysis = comment.sentiment_analysis;
        if (intent !== "all" && analysis?.intent_category !== intent) return false;
        if (sentiment !== "all" && analysis?.sentiment !== sentiment) return false;
        return true;
      }),
    [comments, intent, sentiment],
  );

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const visible = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  const chooseIntent = (value: IntentFilter) => {
    setIntent(value);
    setPage(0);
  };

  return (
    <Panel
      title="Comentários para triagem"
      className="panel--comments"
      description="Amostra com os comentários mais curtidos de cada sentimento. As palavras marcadas são as que o modelo usou para classificar."
    >
      <div className="triage__filters">
        <fieldset className="chips">
          <legend className="visually-hidden">Filtrar por intenção</legend>
          {(["all", ...INTENT_ORDER] as IntentFilter[]).map((value) => (
            <label key={value} className={`chip ${intent === value ? "chip--selected" : ""}`}>
              <input
                type="radio"
                name="intent-filter"
                value={value}
                checked={intent === value}
                onChange={() => chooseIntent(value)}
              />
              {value === "all" ? "Todos" : INTENT_LABELS[value]}
              <span className="chip__count">{formatInteger(counts[value] ?? 0)}</span>
            </label>
          ))}
        </fieldset>
        <label className="select">
          <span className="select__label">Sentimento</span>
          <select
            value={sentiment}
            onChange={(event) => {
              setSentiment(event.target.value as SentimentFilter);
              setPage(0);
            }}
          >
            <option value="all">Todos</option>
            {SENTIMENT_ORDER.map((value) => (
              <option key={value} value={value}>
                {SENTIMENT_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {visible.length === 0 ? (
        <div className="empty empty--action">
          <p>Nenhum comentário da amostra combina com esses filtros.</p>
          <button
            type="button"
            className="button button--ghost"
            onClick={() => {
              chooseIntent("all");
              setSentiment("all");
            }}
          >
            Limpar filtros
          </button>
        </div>
      ) : (
        <ul className="comments" aria-live="polite">
          {visible.map((comment, index) => {
            const analysis = comment.sentiment_analysis;
            return (
              <li key={`${comment.author_username}-${currentPage}-${index}`} className="comment">
                <div className="comment__head">
                  <span className="comment__author">{comment.author_username ?? "Anônimo"}</span>
                  <span className="comment__likes" aria-label={`${comment.likes} curtidas`}>
                    <HeartIcon size={14} />
                    {formatInteger(comment.likes)}
                  </span>
                  {comment.created_at && (
                    <span className="comment__when">{formatRelative(comment.created_at)}</span>
                  )}
                </div>
                <CommentText text={comment.text} phrases={analysis?.key_phrases ?? []} />
                {analysis && (
                  <div className="comment__tags">
                    <span className="tag">
                      <span
                        className="tag__dot"
                        style={{ background: sentimentColor(analysis.sentiment, colors) }}
                        aria-hidden="true"
                      />
                      {SENTIMENT_LABELS[analysis.sentiment]}
                      <span className="tag__muted">{formatPercent(analysis.confidence, 0)} de confiança</span>
                    </span>
                    <span className={`tag tag--intent tag--${analysis.intent_category}`}>
                      {INTENT_LABELS[analysis.intent_category]}
                    </span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {filtered.length > PAGE_SIZE && (
        <nav className="pager" aria-label="Paginação dos comentários">
          <button
            type="button"
            className="button button--ghost"
            onClick={() => setPage(currentPage - 1)}
            disabled={currentPage === 0}
          >
            Anteriores
          </button>
          <span className="pager__status">
            Página {currentPage + 1} de {pages}
          </span>
          <button
            type="button"
            className="button button--ghost"
            onClick={() => setPage(currentPage + 1)}
            disabled={currentPage >= pages - 1}
          >
            Próximos
          </button>
        </nav>
      )}
    </Panel>
  );
}
