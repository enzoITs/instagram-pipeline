import type { AnalysisData } from "../types";

export function reportFilename(data: AnalysisData): string {
  return `analise-instagram-${data.video.instagram_id}-${data.video_id}.json`;
}

/** Downloads the full analysis as pretty-printed JSON (2-space indent). */
export function exportToJSON(data: AnalysisData, filename = reportFilename(data)): void {
  const payload = JSON.stringify({ exported_at: new Date().toISOString(), ...data }, null, 2);
  const blob = new Blob([payload], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoke on the next tick so every browser has started the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
