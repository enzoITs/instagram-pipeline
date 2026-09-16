import type { EngagementBasis, Metrics } from "../../types";
import { engagementTier, formatCompact, formatInteger, formatRate, formatScore } from "../../utils/format";

const BASIS_NOTES: Record<EngagementBasis, string> = {
  reach: "Sobre o alcance (contas que viram o post)",
  views: "Sobre as visualizações",
  followers: "Sobre os seguidores da conta",
};

export default function MetricsRow({ metrics, owned = false }: { metrics: Metrics; owned?: boolean }) {
  const tiles: { label: string; value: string; title?: string }[] = [
    { label: "Visualizações", value: formatCompact(metrics.views), title: formatInteger(metrics.views) },
    { label: "Curtidas", value: formatCompact(metrics.likes), title: formatInteger(metrics.likes) },
    { label: "Comentários", value: formatCompact(metrics.comments), title: formatInteger(metrics.comments) },
  ];
  if (metrics.shares !== null)
    tiles.push({ label: "Compartilhamentos", value: formatCompact(metrics.shares) });
  if (metrics.saves !== null) tiles.push({ label: "Salvamentos", value: formatCompact(metrics.saves) });
  if (metrics.reach !== null)
    tiles.push({ label: "Alcance", value: formatCompact(metrics.reach), title: formatInteger(metrics.reach) });

  const score = metrics.engagement_score;
  const hiddenCounters = metrics.shares === null && metrics.saves === null && metrics.reach === null;

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
            {metrics.engagement_basis
              ? `${BASIS_NOTES[metrics.engagement_basis]} · ${engagementTier(metrics.engagement_rate) ?? ""}`.trim()
              : "Sem denominador confiável (o post não expõe alcance nem visualizações)"}
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
      {hiddenCounters && !owned && (
        <p className="kpis__footnote">
          Alcance, compartilhamentos e salvamentos só são visíveis para o dono do post. Conecte a conta em
          “Minhas contas” para que a análise use os números oficiais.
        </p>
      )}
    </section>
  );
}
