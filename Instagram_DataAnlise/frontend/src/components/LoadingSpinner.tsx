import { useEffect, useState } from "react";

import "../styles/Loading.css";
import { CheckIcon } from "./Icons";

const STEP_LABELS = [
  "Preparando análise",
  "Coletando dados do Instagram",
  "Salvando métricas e comentários",
  "Analisando sentimento, intenção e tópicos",
  "Salvando resultados da análise",
  "Calculando métricas de engajamento",
  "Gerando insights com IA",
  "Finalizando e armazenando em cache",
];

interface Props {
  step: number; // 0 = queued, 1..8 = running step
  totalSteps?: number;
  target?: string | null;
  startedAt: number;
  onCancel: () => void;
}

export default function LoadingSpinner({
  step,
  totalSteps = STEP_LABELS.length,
  target,
  startedAt,
  onCancel,
}: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  const minutes = Math.floor(elapsed / 60);
  const seconds = String(elapsed % 60).padStart(2, "0");
  const current = step === 0 ? "Na fila de processamento" : STEP_LABELS[step - 1];

  return (
    <section className="loading" aria-labelledby="loading-title">
      <div className="loading__head">
        <h1 id="loading-title">Lendo os comentários</h1>
        {target && <p className="loading__target">{target}</p>}
        <p className="loading__note">
          Leva de um a dois minutos. Pode fechar esta aba: a análise continua e fica em Análises recentes.
        </p>
      </div>

      <p className="visually-hidden" aria-live="polite">
        Etapa {step} de {totalSteps}: {current}
      </p>

      <ol className="steps" aria-label="Etapas da análise">
        {STEP_LABELS.slice(0, totalSteps).map((label, index) => {
          const number = index + 1;
          const state = number < step ? "done" : number === step ? "current" : "pending";
          return (
            <li
              key={label}
              className={`steps__item steps__item--${state}`}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="steps__marker" aria-hidden="true">
                {state === "done" ? <CheckIcon size={14} /> : number}
              </span>
              <span className="steps__label">{label}</span>
            </li>
          );
        })}
      </ol>

      <div className="loading__footer">
        <span className="loading__elapsed" role="timer" aria-label="Tempo decorrido">
          {minutes}:{seconds}
        </span>
        <button type="button" className="button button--ghost" onClick={onCancel}>
          Voltar ao início
        </button>
      </div>
    </section>
  );
}
