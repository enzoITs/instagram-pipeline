import type { CollectionRun } from "../../types";
import { formatInteger, formatRelative } from "../../utils/format";

const STATUS_LABELS: Record<CollectionRun["status"], string> = {
  running: "em andamento",
  success: "concluída",
  partial: "parcial",
  failed: "falhou",
};

export default function RunsList({ runs }: { runs: CollectionRun[] }) {
  return (
    <section className="panel" aria-labelledby="runs-title">
      <div className="panel__head">
        <h2 id="runs-title" className="panel__title">
          Últimas coletas
        </h2>
      </div>
      {runs.length === 0 ? (
        <p className="empty">Nenhuma execução registrada ainda.</p>
      ) : (
        <div className="table-scroll">
          <table className="media-table">
            <thead>
              <tr>
                <th scope="col">Quando</th>
                <th scope="col">Origem</th>
                <th scope="col">Situação</th>
                <th scope="col" className="num">
                  Posts
                </th>
                <th scope="col" className="num">
                  Chamadas
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} title={run.error ?? undefined}>
                  <th scope="row">{formatRelative(run.started_at)}</th>
                  <td>{run.trigger === "scheduled" ? "agendada" : "manual"}</td>
                  <td>
                    <span className={`pill pill--${run.status}`}>{STATUS_LABELS[run.status]}</span>
                  </td>
                  <td className="num">{formatInteger(run.media_seen)}</td>
                  <td className="num">{formatInteger(run.api_calls)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
