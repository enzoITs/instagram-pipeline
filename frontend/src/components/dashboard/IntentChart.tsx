import {
  Bar,
  BarChart,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";

import type { IntentDistribution } from "../../types";
import { formatInteger, formatPercent, INTENT_LABELS, INTENT_ORDER } from "../../utils/format";
import { useChartColors } from "../../utils/theme";
import Panel from "./Panel";

interface Row {
  label: string;
  count: number;
  share: number;
}

function IntentTooltip({ active, payload }: TooltipProps<number, string>) {
  const row = payload?.[0]?.payload as Row | undefined;
  if (!active || !row) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__row">
        <strong>{formatInteger(row.count)}</strong>
        <span>
          {row.label} ({formatPercent(row.share)})
        </span>
      </div>
    </div>
  );
}

export default function IntentChart({ distribution }: { distribution: IntentDistribution }) {
  const colors = useChartColors();
  const total = INTENT_ORDER.reduce((sum, key) => sum + distribution[key], 0);
  const rows: Row[] = INTENT_ORDER.map((key) => ({
    label: INTENT_LABELS[key],
    count: distribution[key],
    share: total ? distribution[key] / total : 0,
  })).sort((a, b) => b.count - a.count);

  const table = (
    <table className="data-table">
      <caption className="visually-hidden">Intenção dos comentários</caption>
      <thead>
        <tr>
          <th scope="col">Intenção</th>
          <th scope="col">Comentários</th>
          <th scope="col">Participação</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th scope="row">{row.label}</th>
            <td>{formatInteger(row.count)}</td>
            <td>{formatPercent(row.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Panel
      title="Intenção"
      className="panel--intent"
      description="O que cada comentário quer: comprar, perguntar, elogiar ou atacar."
      table={table}
    >
      {total === 0 ? (
        <p className="empty">Sem comentários analisados.</p>
      ) : (
        <div
          className="intent-chart"
          role="img"
          aria-label="Comentários por intenção; use Ver tabela para os valores"
        >
          <ResponsiveContainer width="100%" height={rows.length * 44 + 8}>
            <BarChart
              layout="vertical"
              data={rows}
              margin={{ top: 4, right: 88, bottom: 4, left: 0 }}
              barCategoryGap={12}
              accessibilityLayer
            >
              <XAxis type="number" hide domain={[0, "dataMax"]} />
              <YAxis
                type="category"
                dataKey="label"
                width={112}
                tickLine={false}
                axisLine={{ stroke: colors["--rule"] }}
                tick={{ fill: colors["--ink-2"], fontSize: 14 }}
              />
              <Tooltip
                cursor={{ fill: colors["--rule"], fillOpacity: 0.35 }}
                content={<IntentTooltip />}
                isAnimationActive={false}
              />
              <Bar
                dataKey="count"
                fill={colors["--series-1"]}
                barSize={20}
                radius={[0, 4, 4, 0]}
                isAnimationActive={false}
              >
                <LabelList
                  dataKey="count"
                  content={(props) => {
                    const {
                      x = 0,
                      y = 0,
                      width = 0,
                      height = 0,
                      value = 0,
                    } = props as {
                      x?: number;
                      y?: number;
                      width?: number;
                      height?: number;
                      value?: number;
                    };
                    const count = Number(value);
                    // Custom renderer: Recharts would wrap the label to the bar's width on short bars.
                    return (
                      <text
                        x={Number(x) + Number(width) + 8}
                        y={Number(y) + Number(height) / 2}
                        dominantBaseline="central"
                        fill={colors["--ink"]}
                        fontSize={14}
                      >
                        {`${formatInteger(count)} (${formatPercent(total ? count / total : 0, 0)})`}
                      </text>
                    );
                  }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
