/** Client-side mirror of backend/app/instagram_url.py (the backend stays the authority). */

const ALLOWED_HOSTS = new Set(["instagram.com", "www.instagram.com", "m.instagram.com"]);
const KIND_ALIASES: Record<string, "reel" | "p"> = { reel: "reel", reels: "reel", p: "p" };
const SHORTCODE = /^[A-Za-z0-9_-]{5,64}$/;

export const URL_HINT = "Use o link de um Reel ou post público, como https://www.instagram.com/reel/ABC123/";

export type UrlValidation = { ok: true; canonicalUrl: string } | { ok: false; message: string };

export function validateInstagramUrl(raw: string): UrlValidation {
  const value = raw.trim();
  if (!value) return { ok: false, message: "Cole o link do Reel ou post que você quer analisar." };
  if (value.length > 2048 || /\s/.test(value)) return { ok: false, message: URL_HINT };

  let parsed: URL;
  try {
    parsed = new URL(value.includes("://") ? value : `https://${value}`);
  } catch {
    return { ok: false, message: URL_HINT };
  }
  const host = parsed.hostname.toLowerCase();
  const hasCredentials = Boolean(parsed.username || parsed.password);
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !ALLOWED_HOSTS.has(host) ||
    parsed.port ||
    hasCredentials
  ) {
    return { ok: false, message: "O link precisa ser do instagram.com. " + URL_HINT };
  }

  let segments = parsed.pathname.split("/").filter(Boolean);
  if (segments.length >= 3 && !(segments[0].toLowerCase() in KIND_ALIASES)) segments = segments.slice(1);
  const kind = KIND_ALIASES[segments[0]?.toLowerCase() ?? ""];
  const shortcode = segments[1];
  if (!kind || !shortcode || !SHORTCODE.test(shortcode)) {
    return { ok: false, message: "Esse link não aponta para um Reel ou post. " + URL_HINT };
  }
  return { ok: true, canonicalUrl: `https://www.instagram.com/${kind}/${shortcode}/` };
}
