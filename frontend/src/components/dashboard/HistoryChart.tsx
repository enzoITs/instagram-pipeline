import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { MetricPoint } from "../../types";
import { formatCompact, formatInteger, formatRate } from "../../utils/format";
import { useChartColors } from "../../utils/theme";
import Panel from "./Panel";

interface Props {
  history: MetricPoint[];
  tracked: boolean;
  busy?: boolean;
  onToggleTracking: (tracked: boolean) => void;
}

const stamp = (iso: string) =>
  new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit" }).format(
    new Date(iso),
  );

/**
 * A link analysis is a photograph of one moment. Tracking turns it into a series:
 * the scheduler re-reads only the counters, the same way connected accounts work.
 */
export default function HistoryChart({ history, tracked, busy = false, onToggleTracking }: Props) {
  const colors = useChartColors();
  const rows = history.map((point) => ({ ...point, when: stamp(point.collected_at) }));

  const table = (
    <table className="data-table">
      <caption className="visually-hidden">Métricas coletadas ao longo do tempo</caption>
      <thead>
        <tr>
          <th scope="col">Coleta</th>
          <th scope="col">Curtidas</th>
          <th scope="col">Comentários</th>
          <th scope="col">Taxa</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.collected_at}>
            <th scope="row">{row.when}</th>
            <td>{formatInteger(row.likes)}</td>
            <td>{formatInteger(row.comments)}</td>
            <td>{formatRate(row.engagement_rate)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Panel
      title="Evolução deste link"
      className="panel--history"
      description="Acompanhar refaz apenas a coleta de números (sem comentários nem NLP) de tempos em tempos."
      table={rows.length > 1 ? table : undefined}
    >
      <div className="filters">
        <button
          type="button"
          className={`button ${tracked ? "button--ghost" : "button--primary"}`}
          onClick={() => onToggleTracking(!tracked)}
          aria-pressed={tracked}
          disabled={busy}
        >
          {tracked ? "Parar de acompanhar" : "Acompanhar este link"}
        </button>
      </div>

      {rows.length <= 1 ? (
        <p className="empty">
          {tracked
            ? "Acompanhando: o segundo ponto aparece na próxima coleta programada."
            : "Só há a medição desta análise. Ative o acompanhamento para ver a evolução."}
        </p>
      ) : (
        <div role="img" aria-label="Evolução das métricas deste link">
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
                dataKey="views"
                name="Visualizações"
                stroke={colors["--series-2"]}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
