import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import Panel from "../../components/dashboard/Panel";
import type { MediaBreakdown } from "../../types";
import { formatCompact, formatInteger, formatRate } from "../../utils/format";
import { useChartColors } from "../../utils/theme";
import { BASIS_LABELS } from "./labels";

const TYPE_LABELS: Record<string, string> = {
  REELS: "Reels",
  FEED: "Feed",
  AD: "Anúncios",
  UNKNOWN: "Outros",
};

export default function BreakdownChart({ rows }: { rows: MediaBreakdown[] }) {
  const colors = useChartColors();
  const data = rows.map((row) => ({
    ...row,
    label: TYPE_LABELS[row.media_product_type] ?? row.media_product_type,
    rate: row.average_engagement_rate ?? 0,
  }));
  const basis = rows.find((row) => row.engagement_basis)?.engagement_basis ?? null;

  const table = (
    <table className="data-table">
      <caption className="visually-hidden">Desempenho por formato</caption>
      <thead>
        <tr>
          <th scope="col">Formato</th>
          <th scope="col">Posts</th>
          <th scope="col">Taxa</th>
          <th scope="col">Alcance</th>
        </tr>
      </thead>
      <tbody>
        {data.map((row) => (
          <tr key={row.media_product_type}>
            <th scope="row">{row.label}</th>
            <td>{formatInteger(row.media_count)}</td>
            <td>{formatRate(row.average_engagement_rate)}</td>
            <td>{formatCompact(row.total_reach)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Panel
      title="Desempenho por formato"
      description={
        basis
          ? `Taxa média ${BASIS_LABELS[basis]}, ponderada pelo tamanho de cada post.`
          : "Compara Reels e posts de feed pela taxa de engajamento."
      }
      table={data.length > 0 ? table : undefined}
    >
      {data.length === 0 ? (
        <p className="empty">Nenhum formato coletado ainda.</p>
      ) : (
        <div role="img" aria-label="Taxa de engajamento média por formato">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }} accessibilityLayer>
              <CartesianGrid stroke={colors["--rule"]} vertical={false} />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={{ stroke: colors["--rule"] }}
                tick={{ fill: colors["--ink-2"], fontSize: 13 }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={56}
                tick={{ fill: colors["--ink-3"], fontSize: 12 }}
                tickFormatter={(value: number) => `${value}%`}
              />
              <Tooltip
                cursor={{ fill: colors["--rule"], fillOpacity: 0.35 }}
                formatter={(value: number) => formatRate(value)}
                isAnimationActive={false}
              />
              <Bar dataKey="rate" fill={colors["--series-2"]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
