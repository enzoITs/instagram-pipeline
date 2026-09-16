import { useEffect, useState } from "react";

import type { RecentItem } from "../types";
import { listRecent, toApiError } from "../utils/api";
import { formatRelative, formatScore } from "../utils/format";
import { AlertIcon, CheckIcon, ClockIcon } from "./Icons";

interface Props {
  refreshKey: number;
  onOpen: (item: RecentItem) => void;
}

const STATUS = {
  completed: { label: "Concluída", Icon: CheckIcon },
  processing: { label: "Em andamento", Icon: ClockIcon },
  failed: { label: "Falhou", Icon: AlertIcon },
} as const;

export default function RecentAnalyses({ refreshKey, onOpen }: Props) {
  const [items, setItems] = useState<RecentItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listRecent(8, 0, controller.signal)
      .then((response) => {
        setItems(response.data);
        setError(null);
      })
      .catch((reason) => {
        const apiError = toApiError(reason);
        if (apiError.code !== "cancelled") setError(apiError.message);
      });
    return () => controller.abort();
  }, [refreshKey]);

  return (
    <section className="recent" aria-labelledby="recent-title">
      <h2 id="recent-title">Análises recentes</h2>
      {error && <p className="recent__empty">Não foi possível carregar o histórico: {error}</p>}
      {!error && items === null && <p className="recent__empty">Carregando histórico…</p>}
      {!error && items?.length === 0 && (
        <p className="recent__empty">
          Nenhuma análise ainda. As próximas aparecem aqui para consulta rápida.
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="recent__list">
          {items.map((item) => {
            const { label, Icon } = STATUS[item.status];
            return (
              <li key={item.id}>
                <button type="button" className="recent__item" onClick={() => onOpen(item)}>
                  <span className="recent__who">
                    <span className="recent__creator">{item.creator_username ?? item.instagram_id}</span>
                    {item.creator_username && <span className="recent__code">{item.instagram_id}</span>}
                  </span>
                  <span className={`status status--${item.status}`}>
                    <Icon size={15} />
                    {label}
                  </span>
                  <span className="recent__score">
                    {item.engagement_score !== null ? `Score ${formatScore(item.engagement_score)}` : ""}
                  </span>
                  <span className="recent__when">{formatRelative(item.created_at)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
