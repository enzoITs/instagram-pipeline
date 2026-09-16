"""Instagram URL parsing and canonicalization (pure functions, no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Canonical form stored and returned by the API (matches the public contract).
CANONICAL_URL_REGEX = re.compile(r"^https://www\.instagram\.com/(reel|p)/[A-Za-z0-9_-]+/?$")

_SHORTCODE_REGEX = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_KIND_ALIASES = {"reel": "reel", "reels": "reel", "p": "p"}

INVALID_URL_MESSAGE = (
    "URL inválida: informe o link de um Reel ou Post do Instagram "
    "(ex: https://www.instagram.com/reel/ABC123/)."
)


class InvalidInstagramURLError(ValueError):
    """Raised when a URL is not a supported Instagram Reel/Post link."""

    def __init__(self, message: str = INVALID_URL_MESSAGE) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InstagramURL:
    canonical_url: str
    shortcode: str
    media_type: str  # "reel" | "post"


def parse_instagram_url(raw_url: str) -> InstagramURL:
    """Normalize user input into the canonical `https://www.instagram.com/{reel|p}/{id}/`.

    Accepted variations (all common when copying links from the app):
    missing scheme, `http`, `instagram.com` / `m.instagram.com` hosts, `/reels/` path,
    `/{username}/reel/{id}/` share links, query strings (`?igsh=...`) and fragments.
    """
    if not isinstance(raw_url, str):
        raise InvalidInstagramURLError()
    candidate = raw_url.strip()
    if not candidate or len(candidate) > 2048 or any(ch.isspace() for ch in candidate):
        raise InvalidInstagramURLError()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"}:
        raise InvalidInstagramURLError()
    host = (parts.hostname or "").lower()
    if host not in _ALLOWED_HOSTS or parts.username or parts.password or parts.port:
        raise InvalidInstagramURLError()

    segments = [segment for segment in parts.path.split("/") if segment]
    # Share links sometimes include the username: /{username}/reel/{shortcode}/
    if len(segments) >= 3 and segments[0].lower() not in _KIND_ALIASES:
        segments = segments[1:]
    # Trailing segments (e.g. comment permalinks `/p/{id}/c/{comment}/`) are ignored.
    if len(segments) < 2:
        raise InvalidInstagramURLError()

    kind = _KIND_ALIASES.get(segments[0].lower())
    shortcode = segments[1]
    if kind is None or not _SHORTCODE_REGEX.match(shortcode):
        raise InvalidInstagramURLError()

    canonical = f"https://www.instagram.com/{kind}/{shortcode}/"
    if not CANONICAL_URL_REGEX.match(canonical):  # defensive: contract regex
        raise InvalidInstagramURLError()
    return InstagramURL(
        canonical_url=canonical,
        shortcode=shortcode,
        media_type="reel" if kind == "reel" else "post",
    )
