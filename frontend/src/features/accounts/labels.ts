import type { EngagementBasis, MediaOrder } from "../../types";

/** Shared constants of the accounts tab (kept out of the component files so
 *  fast refresh keeps working while editing them). */

export const BASIS_LABELS: Record<EngagementBasis, string> = {
  reach: "sobre o alcance",
  views: "sobre as visualizações",
  followers: "sobre os seguidores",
};

/** Ranges offered by the time-series chart, in days. */
export const RANGES = [7, 30, 90, 365];

export const SORTS: { value: string; label: string }[] = [
  { value: "timestamp:desc", label: "Mais recentes" },
  { value: "timestamp:asc", label: "Mais antigos" },
  { value: "engagement_rate:desc", label: "Maior engajamento" },
  { value: "engagement_rate:asc", label: "Menor engajamento" },
  { value: "collected_at:desc", label: "Coletados por último" },
];

export function parseSort(value: string): { orderBy: MediaOrder; direction: "asc" | "desc" } {
  const [orderBy, direction] = value.split(":");
  return { orderBy: orderBy as MediaOrder, direction: direction === "asc" ? "asc" : "desc" };
}
