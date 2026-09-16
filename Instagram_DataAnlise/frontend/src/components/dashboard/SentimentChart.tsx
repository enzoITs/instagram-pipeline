import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";

import type { Sentiment, SentimentDistribution } from "../../types";
import { formatInteger, formatPercent, plural, SENTIMENT_LABELS, SENTIMENT_ORDER } from "../../utils/format";
import { sentimentColor, useChartColors } from "../../utils/theme";
import Panel from "./Panel";

function SentimentTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      {payload
        .filter((entry) => (entry.payload?.[`${entry.dataKey}_count`] ?? 0) > 0)
        .map((entry) => {
          const key = entry.dataKey as Sentiment;
          return (
            <div className="chart-tooltip__row" key={key}>
              <span className="chart-tooltip__key" style={{ background: entry.color }} />
              <strong>{formatPercent(Number(entry.value))}</strong>
              <span>
                {SENTIMENT_LABELS[key]} ({formatInteger(entry.payload[`${key}_count`])})
              </span>
            </div>
          );
        })}
    </div>
  );
}

export default function SentimentChart({ distribution }: { distribution: SentimentDistribution }) {
  const colors = useChartColors();
  const total = SENTIMENT_ORDER.reduce((sum, key) => sum + distribution[key], 0);
  const present = SENTIMENT_ORDER.filter((key) => distribution[key] > 0);
  const row: Record<string, number | string> = { name: "Comentários" };
  for (const key of SENTIMENT_ORDER) {
    row[key] = total ? distribution[key] / total : 0;
    row[`${key}_count`] = distribution[key];
  }

  const table = (
    <table className="data-table">
      <caption className="visually-hidden">Distribuição de sentimento dos comentários</caption>
      <thead>
        <tr>
          <th scope="col">Sentimento</th>
          <th scope="col">Comentários</th>
          <th scope="col">Participação</th>
        </tr>
      </thead>
      <tbody>
        {SENTIMENT_ORDER.map((key) => (
          <tr key={key}>
            <th scope="row">{SENTIMENT_LABELS[key]}</th>
            <td>{formatInteger(distribution[key])}</td>
            <td>{formatPercent(total ? distribution[key] / total : 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <Panel
      title="Sentimento"
      className="panel--sentiment"
      description={`Como ${formatInteger(total)} comentários se dividem por tom.`}
      table={table}
    >
      {total === 0 ? (
        <p className="empty">Sem comentários analisados.</p>
      ) : (
        <>
          <div
            className="sentiment-bar"
            role="img"
            aria-label="Barra de participação por sentimento; valores na legenda abaixo"
          >
            <ResponsiveContainer width="100%" height={40}>
              <BarChart
                layout="vertical"
                data={[row]}
                margin={{ top: 8, right: 0, bottom: 8, left: 0 }}
                accessibilityLayer
              >
                <XAxis type="number" domain={[0, 1]} hide />
                <YAxis type="category" dataKey="name" hide />
                <Tooltip cursor={false} content={<SentimentTooltip />} isAnimationActive={false} />
                {present.map((key, index) => (
                  <Bar
                    key={key}
                    dataKey={key}
                    stackId="sentiment"
                    barSize={24}
                    fill={sentimentColor(key, colors)}
                    stroke={colors["--sheet"]}
                    strokeWidth={2}
                    // Rounded data end only; the baseline (left edge) stays square.
                    radius={index === present.length - 1 ? [0, 4, 4, 0] : 0}
                    isAnimationActive={false}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <ul className="legend">
            {SENTIMENT_ORDER.map((key) => (
              <li key={key} className="legend__item">
                <span
                  className="legend__swatch"
                  style={{ background: sentimentColor(key, colors) }}
                  aria-hidden="true"
                />
                <span className="legend__label">{SENTIMENT_LABELS[key]}</span>
                <span className="legend__value">{formatPercent(distribution[key] / total)}</span>
                <span className="legend__count">
                  {plural(distribution[key], "comentário", "comentários")}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
