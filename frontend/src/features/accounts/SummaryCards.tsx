import type { AccountSummary } from "../../types";
import { formatCompact, formatInteger, formatRate, formatRelative } from "../../utils/format";
import { BASIS_LABELS } from "./labels";

export default function SummaryCards({ summary }: { summary: AccountSummary }) {
  const { account } = summary;
  const cards: { label: string; value: string; note?: string }[] = [
    { label: "Seguidores", value: formatCompact(account.followers_count) },
    {
      label: "Posts monitorados",
      value: formatInteger(summary.tracked_media),
      note: `${formatInteger(summary.snapshots)} coletas no histórico`,
    },
    {
      label: "Taxa de engajamento",
      value: formatRate(summary.average_engagement_rate),
      note: summary.engagement_basis
        ? `Média ponderada ${BASIS_LABELS[summary.engagement_basis]}`
        : "Sem denominador confiável ainda",
    },
    { label: "Alcance total", value: formatCompact(summary.total_reach) },
    { label: "Visualizações", value: formatCompact(summary.total_views) },
    { label: "Curtidas", value: formatCompact(summary.total_likes) },
    { label: "Comentários", value: formatCompact(summary.total_comments) },
    { label: "Salvamentos", value: formatCompact(summary.total_saved) },
    { label: "Compartilhamentos", value: formatCompact(summary.total_shares) },
    {
      label: "Última coleta",
      value: summary.last_collected_at ? formatRelative(summary.last_collected_at) : "Nunca",
      note: summary.first_collected_at ? `desde ${formatRelative(summary.first_collected_at)}` : undefined,
    },
  ];

  return (
    <section className="summary-grid" aria-label="Resumo da conta">
      {cards.map((card) => (
        <div className="summary-card" key={card.label}>
          <p className="summary-card__label">{card.label}</p>
          <p className="summary-card__value">{card.value}</p>
          {card.note && <p className="summary-card__note">{card.note}</p>}
        </div>
      ))}
    </section>
  );
}
