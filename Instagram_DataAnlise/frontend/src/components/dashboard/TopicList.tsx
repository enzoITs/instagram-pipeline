import type { Topic } from "../../types";
import { formatInteger, POLARITY_LABELS, polarityOf } from "../../utils/format";
import Panel from "./Panel";

export default function TopicList({ topics }: { topics: Topic[] }) {
  const max = Math.max(1, ...topics.map((topic) => topic.frequency));

  return (
    <Panel
      title="Tópicos"
      className="panel--topics"
      description="Termos que mais aparecem nos comentários e o tom de quem fala deles."
    >
      {topics.length === 0 ? (
        <p className="empty">Poucos comentários para identificar tópicos recorrentes.</p>
      ) : (
        <ol className="topics">
          {topics.map((topic) => {
            const polarity = polarityOf(topic.sentiment_avg);
            const others = topic.key_keywords.filter((word) => !topic.topic.split(" ").includes(word));
            return (
              <li key={topic.topic} className="topic">
                <div className="topic__line">
                  <span className="topic__name">{topic.topic}</span>
                  <span
                    className={`topic__polarity topic__polarity--${polarity}`}
                    title={`Índice de sentimento ${topic.sentiment_avg.toFixed(2)} (0 = negativo, 1 = positivo)`}
                  >
                    <span className="topic__dot" aria-hidden="true" />
                    {POLARITY_LABELS[polarity]}
                  </span>
                </div>
                <div className="topic__measure">
                  <span
                    className="topic__bar"
                    style={{ width: `${(topic.frequency / max) * 100}%` }}
                    aria-hidden="true"
                  />
                  <span className="topic__count">
                    {formatInteger(topic.frequency)} <span className="visually-hidden">comentários</span>
                  </span>
                </div>
                {others.length > 0 && <p className="topic__keywords">Junto com: {others.join(", ")}</p>}
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}
