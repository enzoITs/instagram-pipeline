import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";

import Panel from "../../components/dashboard/Panel";
import type { TimeseriesPoint } from "../../types";
import { formatCompact, formatInteger, formatRate } from "../../utils/format";
import { useChartColors } from "../../utils/theme";
import { BASIS_LABELS, RANGES } from "./labels";

type MetricKey = "engagement_rate" | "likes" | "comments" | "saved" | "shares" | "reach" | "views";

const METRICS: { key: MetricKey; label: string }[] = [
  { key: "engagement_rate", label: "Taxa de engajamento" },
  { key: "reach", label: "Alcance" },
  { key: "views", label: "Visualizações" },
  { key: "likes", label: "Curtidas" },
  { key: "comments", label: "Comentários" },
  { key: "saved", label: "Salvamentos" },
  { key: "shares", label: "Compartilhamentos" },
];

interface Props {
  points: TimeseriesPoint[];
  days: number;
  onDaysChange: (days: number) => void;
}

const shortDate = (iso: string) =>
  new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit" }).format(
    new Date(iso),
  );

type Row = TimeseriesPoint & { when: string };

function ChartTooltip({
  active,
  payload,
  metric,
  label,
}: TooltipProps<number, string> & { metric: MetricKey; label: string }) {
  const row = payload?.[0]?.payload as Row | undefined;
  if (!active || !row) return null;
  const value = row[metric];
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__row">
        <strong>
          {metric === "engagement_rate"
            ? formatRate(value as number | null)
            : formatInteger(value as number)}
        </strong>
        <span>
          {label} · {row.when} · {formatInteger(row.media_count)} posts
        </span>
      </div>
    </div>
  );
}

export default function TimeseriesChart({ points, days, onDaysChange }: Props) {
  const colors = useChartColors();
  const [metric, setMetric] = useState<MetricKey>("engagement_rate");
  const label = METRICS.find((entry) => entry.key === metric)?.label ?? "";
  const isRate = metric === "engagement_rate";
  const basis = points.find((point) => point.engagement_basis)?.engagement_basis ?? null;

  const rows: Row[] = points.map((point) => ({ ...point, when: shortDate(point.collected_at) }));

  const table = (
    <table className="data-table">
      <caption className="visually-hidden">Métricas por coleta</caption>
      <thead>
        <tr>
          <th scope="col">Coleta</th>
          <th scope="col">{label}</th>
          <th scope="col">Posts</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.collected_at}>
            <th scope="row">{row.when}</th>
            <td>{isRate ? formatRate(row.engagement_rate) : formatInteger(row[metric] as number)}</td>
            <td>{formatInteger(row.media_count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Panel
      title="Evolução"
      description={
        isRate && basis
          ? `Uma linha por coleta. Taxa calculada ${BASIS_LABELS[basis]}.`
          : "Um ponto por coleta: é esse histórico que a Meta não guarda."
      }
      table={rows.length > 0 ? table : undefined}
    >
      <div className="filters">
        <label>
          Período
          <select value={days} onChange={(event) => onDaysChange(Number(event.target.value))}>
            {RANGES.map((range) => (
              <option key={range} value={range}>
                {range} dias
              </option>
            ))}
          </select>
        </label>
        <label>
          Métrica
          <select value={metric} onChange={(event) => setMetric(event.target.value as MetricKey)}>
            {METRICS.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <p className="empty">
          Ainda não há coletas suficientes. Cada execução de “Coletar agora” adiciona um ponto aqui.
        </p>
      ) : (
        <div role="img" aria-label={`${label} por coleta; use Ver tabela para os valores`}>
          <ResponsiveContainer width="100%" height={280}>
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
                width={64}
                tick={{ fill: colors["--ink-3"], fontSize: 12 }}
                tickFormatter={(value: number) => (isRate ? `${value}%` : formatCompact(value))}
              />
              <Tooltip
                content={<ChartTooltip metric={metric} label={label} />}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey={metric}
                stroke={colors["--series-1"]}
                strokeWidth={2}
                dot={rows.length <= 40}
                isAnimationActive={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
