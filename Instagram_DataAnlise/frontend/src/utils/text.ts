import type { AnalysisData } from "../types";
import { formatPercent, plural } from "./format";

export interface TextPart {
  text: string;
  marked: boolean;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Splits `text` into plain and marked parts for every word of the NLP key phrases.
 * Matching is case-insensitive and Unicode-aware (accents count as letters), and the
 * result is rendered as React text nodes, so comment content can never inject markup.
 */
export function highlightKeyPhrases(text: string, phrases: string[]): TextPart[] {
  const words = [
    ...new Set(phrases.flatMap((phrase) => phrase.split(/\s+/)).filter((word) => word.length >= 3)),
  ];
  if (!text || words.length === 0) return [{ text, marked: false }];
  words.sort((a, b) => b.length - a.length);
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])(${words.map(escapeRegExp).join("|")})(?![\\p{L}\\p{N}])`,
    "giu",
  );

  const parts: TextPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push({ text: text.slice(cursor, start), marked: false });
    parts.push({ text: match[0], marked: true });
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), marked: false });
  return parts;
}

/** One plain-language sentence that tells the reader what the comment section is about. */
export function summarize(data: AnalysisData): string {
  const total = data.processing.comments_analyzed;
  if (total === 0) return "Nenhum comentário pôde ser analisado neste post.";

  const buyers = data.intents.potential_buyer;
  const questions = Math.max(data.intents.question, data.sentiment.doubt);
  const negativeRatio = data.sentiment.negative / total;

  const findings: string[] = [];
  if (buyers > 0) findings.push(`${plural(buyers, "quer", "querem")} comprar`);
  if (questions > 0) findings.push(`${plural(questions, "tem", "têm")} dúvidas`);
  if (negativeRatio === 0) {
    findings.push("nenhum tem tom negativo");
  } else {
    findings.push(`${formatPercent(negativeRatio, negativeRatio < 0.01 ? 1 : 0)} têm tom negativo`);
  }

  const list =
    findings.length === 1
      ? findings[0]
      : `${findings.slice(0, -1).join(", ")} e ${findings[findings.length - 1]}`;
  return `${plural(total, "comentário analisado", "comentários analisados")}: ${list}.`;
}
