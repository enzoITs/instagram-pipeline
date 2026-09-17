import { useEffect, useState } from "react";

import { ExternalIcon } from "../../components/Icons";
import type { MediaItem } from "../../types";
import { formatCompact, formatDate, formatInteger, formatRate } from "../../utils/format";
import { SORTS } from "./labels";

interface Props {
  items: MediaItem[];
  total: number;
  offset: number;
  pageSize: number;
  loading: boolean;
  search: string;
  mediaProductType: string;
  sort: string;
  onSearchChange: (value: string) => void;
  onTypeChange: (value: string) => void;
  onSortChange: (value: string) => void;
  onPageChange: (offset: number) => void;
  onOpenHistory: (media: MediaItem) => void;
  onAnalyze: (media: MediaItem) => void;
}

/** Meta's thumbnail URLs expire; a broken image is dropped instead of showing a placeholder. */
function Thumb({ media }: { media: MediaItem }) {
  const [failed, setFailed] = useState(false);
  const source = media.thumbnail_url ?? media.media_url;
  if (!source || failed) return null;
  return (
    <img
      className="media-table__thumb"
      src={source}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

export default function MediaTable({
  items,
  total,
  offset,
  pageSize,
  loading,
  search,
  mediaProductType,
  sort,
  onSearchChange,
  onTypeChange,
  onSortChange,
  onPageChange,
  onOpenHistory,
  onAnalyze,
}: Props) {
  const [draft, setDraft] = useState(search);

  // Debounced search: the table reloads 350 ms after the last keystroke.
  useEffect(() => {
    if (draft === search) return;
    const timer = window.setTimeout(() => onSearchChange(draft), 350);
    return () => window.clearTimeout(timer);
  }, [draft, search, onSearchChange]);

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + pageSize, total);

  return (
    <section className="panel" aria-labelledby="media-title">
      <div className="panel__head">
        <h2 id="media-title" className="panel__title">
          Posts coletados
        </h2>
        <span className="panel__description">
          {formatInteger(total)} no total · {from}–{to}
        </span>
      </div>

      <div className="filters">
        <label>
          Buscar na legenda
          <input
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="palavra-chave"
          />
        </label>
        <label>
          Formato
          <select value={mediaProductType} onChange={(event) => onTypeChange(event.target.value)}>
            <option value="">Todos</option>
            <option value="REELS">Reels</option>
            <option value="FEED">Feed</option>
          </select>
        </label>
        <label>
          Ordenar por
          <select value={sort} onChange={(event) => onSortChange(event.target.value)}>
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="table-scroll">
        <table className="media-table">
          <thead>
            <tr>
              <th scope="col">Post</th>
              <th scope="col">Formato</th>
              <th scope="col">Publicado</th>
              <th scope="col" className="num">
                Curtidas
              </th>
              <th scope="col" className="num">
                Coment.
              </th>
              <th scope="col" className="num">
                Salv.
              </th>
              <th scope="col" className="num">
                Compart.
              </th>
              <th scope="col" className="num">
                Alcance
              </th>
              <th scope="col" className="num">
                Views
              </th>
              <th scope="col" className="num">
                Engaj.
              </th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td colSpan={10}>
                  {loading ? "Carregando posts…" : "Nenhum post coletado ainda para estes filtros."}
                </td>
              </tr>
            )}
            {items.map((media) => {
              const latest = media.latest ?? null;
              const caption = (media.caption ?? "").replace(/\s+/g, " ").slice(0, 90) || "(sem legenda)";
              return (
                <tr key={media.id}>
                  <td>
                    <div className="media-table__post">
                      <Thumb media={media} />
                      <div>
                        <button
                          type="button"
                          className="media-table__caption"
                          onClick={() => onOpenHistory(media)}
                        >
                          {caption}
                        </button>
                        <div className="media-table__links">
                          <button type="button" className="link-button" onClick={() => onAnalyze(media)}>
                            Analisar comentários
                          </button>
                          {media.permalink && (
                            <a href={media.permalink} target="_blank" rel="noopener noreferrer">
                              Abrir <ExternalIcon size={13} />
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className="pill">{media.media_product_type ?? media.media_type ?? "—"}</span>
                  </td>
                  <td>{formatDate(media.timestamp)}</td>
                  <td className="num">{formatCompact(latest?.likes ?? null)}</td>
                  <td className="num">{formatCompact(latest?.comments ?? null)}</td>
                  <td className="num">{formatCompact(latest?.saved ?? null)}</td>
                  <td className="num">{formatCompact(latest?.shares ?? null)}</td>
                  <td className="num">{formatCompact(latest?.reach ?? null)}</td>
                  <td className="num">{formatCompact(latest?.views ?? null)}</td>
                  <td className="num">{formatRate(latest?.engagement_rate ?? null)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {total > pageSize && (
        <nav className="pager" aria-label="Paginação dos posts">
          <button
            type="button"
            className="button button--ghost"
            onClick={() => onPageChange(Math.max(0, offset - pageSize))}
            disabled={offset === 0 || loading}
          >
            Anteriores
          </button>
          <span className="pager__status">
            {from}–{to} de {formatInteger(total)}
          </span>
          <button
            type="button"
            className="button button--ghost"
            onClick={() => onPageChange(offset + pageSize)}
            disabled={to >= total || loading}
          >
            Próximos
          </button>
        </nav>
      )}
    </section>
  );
}
