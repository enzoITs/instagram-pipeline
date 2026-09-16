import type { Metrics } from "../../types";
import { engagementTier, formatCompact, formatInteger, formatRate, formatScore } from "../../utils/format";

export default function MetricsRow({ metrics }: { metrics: Metrics }) {
  const tiles: { label: string; value: string; title?: string }[] = [
    { label: "Visualizações", value: formatCompact(metrics.views), title: formatInteger(metrics.views) },
    { label: "Curtidas", value: formatCompact(metrics.likes), title: formatInteger(metrics.likes) },
    { label: "Comentários", value: formatCompact(metrics.comments), title: formatInteger(metrics.comments) },
  ];
  if (metrics.shares !== null)
    tiles.push({ label: "Compartilhamentos", value: formatCompact(metrics.shares) });
  if (metrics.saves !== null) tiles.push({ label: "Salvamentos", value: formatCompact(metrics.saves) });

  const score = metrics.engagement_score;
  const hiddenCounters = metrics.shares === null && metrics.saves === null;

  return (
    <section className="kpis" aria-label="Métricas de engajamento">
      <dl className="kpis__grid">
        {tiles.map((tile) => (
          <div className="kpi" key={tile.label}>
            <dt className="kpi__label">{tile.label}</dt>
            <dd className="kpi__value" title={tile.title}>
              {tile.value}
            </dd>
          </div>
        ))}
        <div className="kpi">
          <dt className="kpi__label">Taxa de engajamento</dt>
          <dd className="kpi__value">{formatRate(metrics.engagement_rate)}</dd>
          <dd className="kpi__note">
            {engagementTier(metrics.engagement_rate) ?? "Sem visualizações públicas"}
          </dd>
        </div>
        <div className="kpi kpi--score">
          <dt className="kpi__label">Score de engajamento</dt>
          <dd className="kpi__value">
            {formatScore(score)}
            {score !== null && <span className="kpi__unit"> de 10</span>}
          </dd>
          {score !== null && (
            <dd>
              <div
                className="meter"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={10}
                aria-valuenow={score}
                aria-label="Score de engajamento"
              >
                <span className="meter__fill" style={{ width: `${(score / 10) * 100}%` }} />
              </div>
            </dd>
          )}
        </div>
      </dl>
      {hiddenCounters && (
        <p className="kpis__footnote">
          Compartilhamentos e salvamentos não são públicos no Instagram, então ficam fora da taxa de
          engajamento.
        </p>
      )}
    </section>
  );
}
