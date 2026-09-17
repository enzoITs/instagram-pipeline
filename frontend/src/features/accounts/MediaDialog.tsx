import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { MediaDetail, MediaItem } from "../../types";
import { readMedia } from "../../utils/accountsApi";
import { formatCompact, formatDate, formatInteger, formatRate } from "../../utils/format";
import { toApiError } from "../../utils/http";
import { useChartColors } from "../../utils/theme";

interface Props {
  media: MediaItem;
  onClose: () => void;
  onAnalyze: (media: MediaItem) => void;
}

const stamp = (iso: string) =>
  new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit" }).format(
    new Date(iso),
  );

export default function MediaDialog({ media, onClose, onAnalyze }: Props) {
  const colors = useChartColors();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<MediaDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialogRef.current?.showModal?.();
    const controller = new AbortController();
    readMedia(media.id, controller.signal)
      .then(setDetail)
      .catch((reason) => {
        const apiError = toApiError(reason);
        if (apiError.code !== "cancelled") setError(apiError.message);
      });
    return () => controller.abort();
  }, [media.id]);

  const snapshots = detail?.snapshots ?? [];
  const rows = snapshots.map((snapshot) => ({ ...snapshot, when: stamp(snapshot.collected_at) }));

  return (
    <dialog className="media-dialog" ref={dialogRef} onClose={onClose} aria-labelledby="media-dialog-title">
      <div className="media-dialog__head">
        <h2 id="media-dialog-title" className="panel__title">
          {(media.caption ?? "").replace(/\s+/g, " ").slice(0, 70) || "Histórico do post"}
        </h2>
        <div className="toolbar__actions">
          <button type="button" className="button button--ghost" onClick={() => onAnalyze(media)}>
            Analisar comentários
          </button>
          <button type="button" className="button button--ghost" onClick={onClose}>
            Fechar
          </button>
        </div>
      </div>

      <div className="media-dialog__body">
        {error && <p className="empty">{error}</p>}
        {!error && detail === null && <p className="empty">Carregando histórico…</p>}
        {detail && (
          <>
            <p className="panel__description">
              {detail.media_product_type ?? detail.media_type ?? "—"} · publicado em{" "}
              {formatDate(detail.timestamp)} · {formatInteger(snapshots.length)} coleta(s)
            </p>

            {rows.length > 1 ? (
              <div role="img" aria-label="Evolução das métricas deste post">
                <ResponsiveContainer width="100%" height={240}>
                  <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }} accessibilityLayer>
                    <CartesianGrid stroke={colors["--rule"]} vertical={false} />
                    <XAxis
                      dataKey="when"
                      tickLine={false}
                      axisLine={{ stroke: colors["--rule"] }}
                      tick={{ fill: colors["--ink-3"], fontSize: 12 }}
                    />
                    <YAxis
                      tickLine={false}
                      axisLine={false}
                      width={56}
                      tick={{ fill: colors["--ink-3"], fontSize: 12 }}
                      tickFormatter={(value: number) => formatCompact(value)}
                    />
                    <Tooltip isAnimationActive={false} />
                    <Line
                      type="monotone"
                      dataKey="likes"
                      name="Curtidas"
                      stroke={colors["--series-1"]}
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="reach"
                      name="Alcance"
                      stroke={colors["--series-2"]}
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="saved"
                      name="Salvamentos"
                      stroke={colors["--series-3"]}
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="empty">O gráfico aparece a partir da segunda coleta deste post.</p>
            )}

            <div className="table-scroll">
              <table className="media-table">
                <caption className="visually-hidden">Histórico de métricas do post</caption>
                <thead>
                  <tr>
                    <th scope="col">Coleta</th>
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
                  {[...rows].reverse().map((snapshot) => (
                    <tr key={snapshot.id}>
                      <th scope="row">{snapshot.when}</th>
                      <td className="num">{formatInteger(snapshot.likes)}</td>
                      <td className="num">{formatInteger(snapshot.comments)}</td>
                      <td className="num">{formatInteger(snapshot.saved)}</td>
                      <td className="num">{formatInteger(snapshot.shares)}</td>
                      <td className="num">{formatInteger(snapshot.reach)}</td>
                      <td className="num">{formatInteger(snapshot.views)}</td>
                      <td className="num">{formatRate(snapshot.engagement_rate)}</td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={8}>Sem coletas para este post.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
