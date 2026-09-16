import type { Insight, InsightType } from "../../types";
import { formatScore, INSIGHT_LABELS } from "../../utils/format";
import { ImprovementIcon, StrengthIcon, TrendIcon } from "../Icons";
import Panel from "./Panel";

const ICONS: Record<InsightType, typeof StrengthIcon> = {
  strength: StrengthIcon,
  improvement: ImprovementIcon,
  trend: TrendIcon,
};

const SOURCE_NOTES: Record<string, string> = {
  openai: "Gerados por IA a partir das métricas e de uma amostra dos comentários.",
  "openai+rules": "Gerados por IA e completados por regras de análise.",
  rules: "Gerados por regras de análise (IA indisponível ou não configurada).",
};

export default function InsightList({ insights, source }: { insights: Insight[]; source: string | null }) {
  return (
    <Panel
      title="O que fazer com isso"
      className="panel--insights"
      description={SOURCE_NOTES[source ?? ""] ?? "Recomendações ordenadas por impacto."}
    >
      <ol className="insights">
        {insights.map((insight, index) => {
          const Icon = ICONS[insight.type];
          // Two insights can share a title; the index keeps the keys unique.
          const impact = Math.min(10, Math.max(0, insight.impact_score));
          return (
            <li key={`${insight.title}-${index}`} className={`insight insight--${insight.type}`}>
              <p className="insight__type">
                <Icon size={16} />
                {INSIGHT_LABELS[insight.type]}
              </p>
              <h3 className="insight__title">{insight.title}</h3>
              <p className="insight__description">{insight.description}</p>
              <div className="insight__impact">
                <span>Impacto {formatScore(insight.impact_score)} de 10</span>
                <div
                  className="meter meter--small"
                  role="meter"
                  aria-valuemin={0}
                  aria-valuemax={10}
                  aria-valuenow={impact}
                  aria-label={`Impacto de ${insight.title}`}
                >
                  <span className="meter__fill" style={{ width: `${impact * 10}%` }} />
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}
