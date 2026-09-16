import { useEffect, useState } from "react";

import type { Sentiment } from "../types";
import { SENTIMENT_ORDER } from "./format";

export type Theme = "light" | "dark";
const STORAGE_KEY = "analyzer-theme";

export const CHART_TOKENS = [
  "--series-1",
  "--series-2",
  "--series-3",
  "--series-4",
  "--series-5",
  "--sheet",
  "--ink",
  "--ink-2",
  "--ink-3",
  "--rule",
] as const;

export type ChartColors = Record<(typeof CHART_TOKENS)[number], string>;

/** Light-mode fallbacks (same values as styles/global.css) for environments without CSS. */
const FALLBACK: ChartColors = {
  "--series-1": "#2a78d6",
  "--series-2": "#eb6834",
  "--series-3": "#1baf7a",
  "--series-4": "#eda100",
  "--series-5": "#e87ba4",
  "--sheet": "#ffffff",
  "--ink": "#15232d",
  "--ink-2": "#4b5b66",
  "--ink-3": "#5b6972",
  "--rule": "#d6dee3",
};

function readColors(): ChartColors {
  const styles = getComputedStyle(document.documentElement);
  return Object.fromEntries(
    CHART_TOKENS.map((token) => [token, styles.getPropertyValue(token).trim() || FALLBACK[token]]),
  ) as ChartColors;
}

export function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function applyTheme(theme: Theme | null): void {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try {
    if (theme) localStorage.setItem(STORAGE_KEY, theme);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage may be blocked; the choice still applies to this page view */
  }
  window.dispatchEvent(new Event("analyzer-theme-change"));
}

export function currentTheme(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * SVG presentation attributes cannot reliably resolve CSS variables, so charts read the
 * resolved token values and re-render when the theme (explicit or OS-level) changes.
 */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readColors);

  useEffect(() => {
    const update = () => setColors(readColors());
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener?.("change", update);
    window.addEventListener("analyzer-theme-change", update);
    return () => {
      media?.removeEventListener?.("change", update);
      window.removeEventListener("analyzer-theme-change", update);
    };
  }, []);

  return colors;
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    const update = () => setTheme(currentTheme());
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener?.("change", update);
    window.addEventListener("analyzer-theme-change", update);
    return () => {
      media?.removeEventListener?.("change", update);
      window.removeEventListener("analyzer-theme-change", update);
    };
  }, []);

  return [theme, (next: Theme) => applyTheme(next)];
}

/** Categorical slots follow SENTIMENT_ORDER, the validated adjacent order of the palette. */
export function sentimentColor(sentiment: Sentiment, colors: ChartColors): string {
  const slot = SENTIMENT_ORDER.indexOf(sentiment) + 1;
  return colors[`--series-${slot}` as keyof ChartColors];
}
