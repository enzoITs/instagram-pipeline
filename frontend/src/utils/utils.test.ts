import { describe, expect, it, vi } from "vitest";

import { makeAnalysis } from "../test/fixtures";
import { exportToJSON, reportFilename } from "./export";
import {
  engagementTier,
  formatCompact,
  formatDate,
  formatInteger,
  formatPercent,
  formatRate,
  formatRelative,
  formatScore,
  plural,
  polarityOf,
} from "./format";
import { highlightKeyPhrases, summarize } from "./text";
import { applyTheme, currentTheme, readStoredTheme, sentimentColor } from "./theme";
import { validateInstagramUrl } from "./validation";

describe("validateInstagramUrl", () => {
  it.each([
    ["https://www.instagram.com/reel/ABC123/", "https://www.instagram.com/reel/ABC123/"],
    ["instagram.com/reels/XYZ987?igsh=1", "https://www.instagram.com/reel/XYZ987/"],
    ["http://m.instagram.com/p/QWERTY1", "https://www.instagram.com/p/QWERTY1/"],
    ["https://www.instagram.com/brand/reel/DEF4567/", "https://www.instagram.com/reel/DEF4567/"],
  ])("accepts %s", (raw, canonical) => {
    expect(validateInstagramUrl(raw)).toEqual({ ok: true, canonicalUrl: canonical });
  });

  it.each([
    "",
    "youtube.com/watch?v=1",
    "https://www.instagram.com/stories/x/1/",
    "https://www.instagram.com/reel/ab/",
    "not a url",
    "https://www.instagram.com:8080/reel/ABC123/",
    "https://%zz",
  ])("rejects %s", (raw) => {
    const result = validateInstagramUrl(raw);
    expect(result.ok).toBe(false);
  });
});

describe("format", () => {
  it("formats numbers the Brazilian way", () => {
    expect(formatInteger(1284)).toBe("1.284");
    expect(formatInteger(null)).toBe("N/D");
    expect(formatCompact(9999)).toBe("9.999");
    expect(formatCompact(12900)).toMatch(/^12,9\s?mil$/);
    expect(formatCompact(4_200_000)).toMatch(/^4,2\s?mi$/);
    expect(formatCompact(undefined)).toBe("N/D");
    expect(formatPercent(0.4361)).toBe("43,6%");
    expect(formatPercent(0.4361, 0)).toBe("44%");
    expect(formatRate(10.2)).toBe("10,20%");
    expect(formatRate(null)).toBe("N/D");
    expect(formatScore(6.55)).toMatch(/^6,[56]$/);
    expect(formatScore(null)).toBe("N/D");
    expect(plural(1, "comentário", "comentários")).toBe("1 comentário");
    expect(plural(2000, "comentário", "comentários")).toBe("2.000 comentários");
  });

  it("formats dates", () => {
    const now = new Date("2026-09-10T12:00:00Z");
    expect(formatRelative("2026-09-10T11:55:00Z", now)).toBe("há 5 minutos");
    expect(formatRelative("2026-09-10T11:59:50Z", now)).toBe("agora mesmo");
    expect(formatRelative("2026-09-08T12:00:00Z", now)).toBe("anteontem");
    expect(formatRelative(null)).toBe("");
    expect(formatDate("2026-08-15T10:30:00Z")).toContain("2026");
    expect(formatDate(null)).toBe("");
  });

  it("classifies polarity and engagement tiers", () => {
    expect(polarityOf(0.92)).toBe("positive");
    expect(polarityOf(0.5)).toBe("neutral");
    expect(polarityOf(0.2)).toBe("negative");
    expect(engagementTier(null)).toBeNull();
    expect(engagementTier(1)).toContain("baixo");
    expect(engagementTier(3)).toContain("médio");
    expect(engagementTier(7)).toContain("bom");
    expect(engagementTier(12)).toContain("excelente");
  });
});

describe("highlightKeyPhrases", () => {
  it("marks whole words case- and accent-aware", () => {
    const parts = highlightKeyPhrases("Adorei! Quando vende? Produto INCRÍVEL, vendedor bom", [
      "vende",
      "produto incrível",
    ]);
    expect(parts.filter((part) => part.marked).map((part) => part.text)).toEqual([
      "vende",
      "Produto",
      "INCRÍVEL",
    ]);
    expect(parts.map((part) => part.text).join("")).toBe(
      "Adorei! Quando vende? Produto INCRÍVEL, vendedor bom",
    );
  });

  it("returns the text untouched without usable phrases", () => {
    expect(highlightKeyPhrases("oi", [])).toEqual([{ text: "oi", marked: false }]);
    expect(highlightKeyPhrases("a b", ["a"])).toEqual([{ text: "a b", marked: false }]);
    expect(highlightKeyPhrases("preço (R$)", ["(r$)", "preço"])[0]).toEqual({ text: "preço", marked: true });
  });
});

describe("summarize", () => {
  it("writes one sentence with the key findings", () => {
    expect(summarize(makeAnalysis())).toBe(
      "200 comentários analisados: 25 querem comprar, 25 têm dúvidas e 15% têm tom negativo.",
    );
  });

  it("handles singular, zero and tiny values", () => {
    const base = makeAnalysis();
    const single = makeAnalysis({
      intents: { ...base.intents, potential_buyer: 1, question: 0 },
      sentiment: { ...base.sentiment, doubt: 0, negative: 0 },
      processing: { ...base.processing, comments_analyzed: 1 },
    });
    expect(summarize(single)).toBe("1 comentário analisado: 1 quer comprar e nenhum tem tom negativo.");

    const tiny = makeAnalysis({
      intents: { ...base.intents, potential_buyer: 0, question: 0 },
      sentiment: { ...base.sentiment, doubt: 0, negative: 1 },
      processing: { ...base.processing, comments_analyzed: 400 },
    });
    expect(summarize(tiny)).toBe("400 comentários analisados: 0,3% têm tom negativo.");

    const empty = makeAnalysis({ processing: { ...base.processing, comments_analyzed: 0 } });
    expect(summarize(empty)).toBe("Nenhum comentário pôde ser analisado neste post.");
  });
});

describe("exportToJSON", () => {
  it("downloads pretty-printed JSON named after the video", async () => {
    const data = makeAnalysis();
    const createUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:report");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      expect(this.download).toBe("analise-instagram-C8xYz12AbCd-7b1e3c1e-1111-4f5e-9a1a-1234567890ab.json");
      expect(this.href).toBe("blob:report");
    });

    exportToJSON(data);

    expect(click).toHaveBeenCalledTimes(1);
    const blob = createUrl.mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('\n  "video_id": "7b1e3c1e-1111-4f5e-9a1a-1234567890ab"');
    expect(JSON.parse(text).metrics.views).toBe(250000);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revoke).toHaveBeenCalledWith("blob:report");
    expect(document.querySelector("a[download]")).toBeNull();
    expect(reportFilename(data)).toMatch(/\.json$/);
  });
});

describe("theme", () => {
  it("applies, persists and reads the explicit theme", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(readStoredTheme()).toBe("dark");
    expect(currentTheme()).toBe("dark");
    applyTheme(null);
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(readStoredTheme()).toBeNull();
    expect(currentTheme()).toBe("light");
  });

  it("maps sentiments to validated palette slots", () => {
    const colors = {
      "--series-1": "a",
      "--series-2": "b",
      "--series-3": "c",
      "--series-4": "d",
      "--series-5": "e",
    };
    expect(sentimentColor("positive", colors as never)).toBe("a");
    expect(sentimentColor("spam", colors as never)).toBe("e");
  });
});
