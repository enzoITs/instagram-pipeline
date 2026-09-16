import type { Intent, InsightType, Sentiment } from "../types";

const LOCALE = "pt-BR";

const integer = new Intl.NumberFormat(LOCALE);
const compact = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 });
const oneDecimal = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Also the categorical slot order (series-1..5), validated for adjacent CVD separation. */
export const SENTIMENT_ORDER: Sentiment[] = ["positive", "negative", "neutral", "doubt", "spam"];
export const INTENT_ORDER: Intent[] = ["potential_buyer", "question", "praise", "hater", "other"];

export const SENTIMENT_LABELS: Record<Sentiment, string> = {
  positive: "Positivo",
  negative: "Negativo",
  neutral: "Neutro",
  doubt: "Dúvida",
  spam: "Spam",
};

export const INTENT_LABELS: Record<Intent, string> = {
  potential_buyer: "Quer comprar",
  question: "Pergunta",
  praise: "Elogio",
  hater: "Hater",
  other: "Outros",
};

export const INSIGHT_LABELS: Record<InsightType, string> = {
  strength: "Ponto forte",
  improvement: "A melhorar",
  trend: "Tendência",
};

export function formatInteger(value: number | null | undefined): string {
  return value === null || value === undefined ? "N/D" : integer.format(value);
}

/** 1.284 / 12,9 mil / 4,2 mi */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "N/D";
  return Math.abs(value) < 10_000 ? integer.format(value) : compact.format(value);
}

export function formatPercent(ratio: number, digits: 0 | 1 = 1): string {
  const value = ratio * 100;
  return `${digits === 0 ? Math.round(value) : oneDecimal.format(value)}%`;
}

export function formatRate(value: number | null): string {
  return value === null ? "N/D" : `${twoDecimals.format(value)}%`;
}

export function formatScore(value: number | null): string {
  return value === null ? "N/D" : oneDecimal.format(value);
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short", year: "numeric" }).format(
    new Date(iso),
  );
}

export function formatRelative(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.trunc(seconds / size), unit);
  }
  return "agora mesmo";
}

export function plural(count: number, singular: string, pluralForm: string): string {
  return `${integer.format(count)} ${count === 1 ? singular : pluralForm}`;
}

export type Polarity = "positive" | "neutral" | "negative";

/** sentiment_avg: 0 = negative, 0.5 = neutral, 1 = positive */
export function polarityOf(sentimentAvg: number): Polarity {
  if (sentimentAvg >= 0.6) return "positive";
  if (sentimentAvg < 0.4) return "negative";
  return "neutral";
}

export const POLARITY_LABELS: Record<Polarity, string> = {
  positive: "positivo",
  neutral: "neutro",
  negative: "negativo",
};

/** Same tiers as backend PerformanceAnalyzer (per-view engagement benchmarks for Reels). */
export function engagementTier(rate: number | null): string | null {
  if (rate === null) return null;
  if (rate < 2) return "Nível baixo (abaixo de 2%)";
  if (rate < 5) return "Nível médio (2% a 5%)";
  if (rate < 10) return "Nível bom (5% a 10%)";
  return "Nível excelente (10% ou mais)";
}
