import { useId, useState, type ReactNode } from "react";

interface Props {
  title: string;
  description?: ReactNode;
  className?: string;
  /** When provided, the panel offers a chart/table toggle (the accessible twin of the chart). */
  table?: ReactNode;
  children: ReactNode;
}

export default function Panel({ title, description, className = "", table, children }: Props) {
  const [showTable, setShowTable] = useState(false);
  const headingId = useId();

  return (
    <section className={`panel ${className}`} aria-labelledby={headingId}>
      <div className="panel__head">
        <h2 id={headingId} className="panel__title">
          {title}
        </h2>
        {table && (
          <button
            type="button"
            className="link-button"
            aria-pressed={showTable}
            onClick={() => setShowTable((value) => !value)}
          >
            {showTable ? "Ver gráfico" : "Ver tabela"}
          </button>
        )}
      </div>
      {description && <p className="panel__description">{description}</p>}
      {table && showTable ? table : children}
    </section>
  );
}
