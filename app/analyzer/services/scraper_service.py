"""Instagram data collection.

Strategy chain (SCRAPER_MODE=auto): Apify REST API (primary, needs APIFY_TOKEN) and
Instaloader (fallback). `mock` generates deterministic synthetic data for demos and
tests; it is always flagged as `source="mock"` downstream.

Note on metrics: Instagram does not expose share/save counts publicly, so scrapers
return them as None ("not available") unless the provider supplies them.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import random
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.instagram.url import InstagramURL, parse_instagram_url

logger = logging.getLogger(__name__)


MAX_COMMENT_LENGTH = 2200  # Instagram's own limit
_TRANSIENT_HTTP_STATUS = {408, 425, 429}


@dataclass(slots=True)
class ScrapedComment:
    comment_id: str
    text: str
    author: str | None = None
    likes: int = 0
    created_at: datetime | None = None


@dataclass(slots=True)
class ScrapedPost:
    instagram_id: str
    url: str
    media_type: str
    source: str
    caption: str | None = None
    creator_username: str | None = None
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_sec: float | None = None
    views: int | None = None
    likes: int | None = None
    comments_count: int | None = None
    shares: int | None = None
    saves: int | None = None
    # Only the owner of the post can see these two, through the Graph API.
    reach: int | None = None
    followers_count: int | None = None
    comments: list[ScrapedComment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ScraperError(Exception):
    """Scraping failure. `transient=True` means retrying later may succeed."""

    def __init__(self, message: str, *, transient: bool, source: str | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.source = source


# --------------------------------------------------------------------------- helpers
def to_count(value: Any) -> int | None:
    """Parse a non-negative counter; negative values (hidden counters) become None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_timestamp(value: Any) -> datetime | None:
    """Accept ISO-8601 strings, epoch seconds/milliseconds or datetimes; return aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10**11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _first_not_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def retry_call[T](
    func: Callable[[float], T],
    *,
    attempts: int,
    deadline: float,
    operation: str,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call `func(remaining_seconds)` with exponential backoff (1s, 2s, 4s... + jitter).

    Only transient ScraperErrors are retried, and never beyond `deadline`
    (a `time.monotonic()` value), so the whole scraping step stays within budget.
    """
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise ScraperError(f"{operation}: orçamento de tempo esgotado", transient=True)
        try:
            return func(remaining)
        except ScraperError as exc:
            if not exc.transient or attempt >= attempts:
                raise
            delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            if time.monotonic() + delay >= deadline:
                raise
            logger.warning(
                "scraper_retry",
                extra={"operation": operation, "attempt": attempt, "delay_s": round(delay, 2), "error": str(exc)},
            )
            (sleep or time.sleep)(delay)
    raise ScraperError(f"{operation}: tentativas esgotadas", transient=True)  # pragma: no cover


class InstagramScraper:
    """Collects post metadata, engagement metrics and comments for one Reel/Post."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
        instaloader_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = http_client or httpx.Client(follow_redirects=True)
        self._owns_http = http_client is None
        self._instaloader_factory = instaloader_factory

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> InstagramScraper:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ URL helpers
    @staticmethod
    def validate_url(url: str) -> str:
        """Return the canonical URL or raise InvalidInstagramURLError."""
        return parse_instagram_url(url).canonical_url

    @staticmethod
    def extract_instagram_id(url: str) -> str:
        return parse_instagram_url(url).shortcode

    # ------------------------------------------------------------------ orchestration
    def scrape(self, url: str) -> ScrapedPost:
        parsed = parse_instagram_url(url)
        strategies = self._strategies()
        errors: list[ScraperError] = []
        started = time.perf_counter()

        for name, strategy in strategies:
            try:
                post = strategy(parsed)
            except ScraperError as exc:
                errors.append(exc)
                logger.warning(
                    "scraper_strategy_failed",
                    extra={"source": name, "transient": exc.transient, "error": str(exc)},
                )
                continue
            if errors:
                post.warnings.append(
                    f"Fonte primária indisponível ({errors[-1]}); dados coletados via {name}."
                )
            logger.info(
                "scraper_succeeded",
                extra={
                    "source": name,
                    "instagram_id": parsed.shortcode,
                    "comments": len(post.comments),
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return post

        transient = any(error.transient for error in errors)
        details = "; ".join(f"{error.source or 'scraper'}: {error}" for error in errors)
        raise ScraperError(f"Não foi possível coletar dados do Instagram ({details})", transient=transient)

    def _strategies(self) -> list[tuple[str, Callable[[InstagramURL], ScrapedPost]]]:
        mode = self.settings.scraper_mode
        if mode == "mock":
            return [("mock", self._scrape_mock)]
        if mode == "apify":
            return [("apify", self._scrape_with_apify)]
        if mode == "instaloader":
            return [("instaloader", self._scrape_with_instaloader)]
        chain: list[tuple[str, Callable[[InstagramURL], ScrapedPost]]] = []
        if self.settings.apify_token:
            chain.append(("apify", self._scrape_with_apify))
        chain.append(("instaloader", self._scrape_with_instaloader))
        return chain

    # ------------------------------------------------------------------ Apify
    def _scrape_with_apify(self, parsed: InstagramURL) -> ScrapedPost:
        if not self.settings.apify_token:
            raise ScraperError("APIFY_TOKEN não configurado", transient=False, source="apify")

        deadline = time.monotonic() + self.settings.scraper_budget_seconds
        post_input = {
            "directUrls": [parsed.canonical_url],
            "resultsType": "posts",
            "resultsLimit": 1,
            "addParentData": False,
        }
        comments_input = {
            "directUrls": [parsed.canonical_url],
            "resultsLimit": self.settings.max_comments,
            "includeNestedComments": False,
        }

        def call(actor: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
            return retry_call(
                lambda remaining: self._run_apify_actor(actor, run_input, remaining),
                attempts=self.settings.apify_max_attempts,
                deadline=deadline,
                operation=f"apify:{actor}",
            )

        # Metadata and comments are independent actor runs: fetch them in parallel.
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="apify")
        try:
            post_future = pool.submit(call, self.settings.apify_post_actor, post_input)
            comments_future = pool.submit(call, self.settings.apify_comments_actor, comments_input)
            post_items = post_future.result()
            comment_items: list[dict[str, Any]] | None
            warnings: list[str] = []
            try:
                comment_items = comments_future.result()
            except ScraperError as exc:
                comment_items = None
                warnings.append(f"Coleta completa de comentários falhou ({exc}); usando amostra do post.")
        finally:
            # Do not block a fallback on a still-running sibling request.
            pool.shutdown(wait=False, cancel_futures=True)

        post = self._normalize_apify_response(post_items, comment_items, parsed)
        post.warnings.extend(warnings)
        return post

    def _run_apify_actor(self, actor: str, run_input: dict[str, Any], remaining: float) -> list[dict[str, Any]]:
        assert self.settings.apify_token is not None
        actor_timeout = int(max(5, min(self.settings.apify_timeout_seconds, remaining - 5)))
        endpoint = f"{self.settings.apify_base_url}/acts/{actor}/run-sync-get-dataset-items"
        try:
            response = self._http.post(
                endpoint,
                params={"timeout": actor_timeout, "format": "json", "clean": "true"},
                json=run_input,
                # Token goes in a header, never in the URL (URLs end up in logs).
                headers={"Authorization": f"Bearer {self.settings.apify_token.get_secret_value()}"},
                timeout=httpx.Timeout(actor_timeout + 15, connect=10),
            )
        except httpx.TimeoutException as exc:
            raise ScraperError(f"timeout após {actor_timeout}s", transient=True, source="apify") from exc
        except httpx.HTTPError as exc:
            raise ScraperError(f"erro de conexão ({type(exc).__name__})", transient=True, source="apify") from exc

        status = response.status_code
        if status in _TRANSIENT_HTTP_STATUS or status >= 500:
            raise ScraperError(f"HTTP {status} temporário", transient=True, source="apify")
        if status >= 400:
            hint = {
                401: "token inválido",
                402: "créditos insuficientes",
                403: "acesso negado",
                404: "actor inexistente",
            }
            raise ScraperError(
                f"HTTP {status} ({hint.get(status, 'requisição rejeitada')})", transient=False, source="apify"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ScraperError("resposta JSON inválida", transient=True, source="apify") from exc
        if not isinstance(payload, list):
            raise ScraperError("formato de resposta inesperado", transient=False, source="apify")
        return [item for item in payload if isinstance(item, dict)]

    def _normalize_apify_response(
        self,
        post_items: list[dict[str, Any]],
        comment_items: list[dict[str, Any]] | None,
        parsed: InstagramURL,
    ) -> ScrapedPost:
        errors = [item for item in post_items if item.get("error")]
        posts = [item for item in post_items if not item.get("error") and (item.get("shortCode") or item.get("id"))]
        if not posts:
            reason = (errors[0].get("errorDescription") or errors[0].get("error")) if errors else "sem resultados"
            raise ScraperError(f"post indisponível ({reason})", transient=False, source="apify")

        item = next((p for p in posts if p.get("shortCode") == parsed.shortcode), posts[0])
        raw_owner = item.get("owner")
        owner: dict[str, Any] = raw_owner if isinstance(raw_owner, dict) else {}
        raw_comments: Iterable[dict[str, Any]] = (
            comment_items if comment_items is not None else item.get("latestComments") or []
        )
        return ScrapedPost(
            instagram_id=parsed.shortcode,
            url=parsed.canonical_url,
            media_type=parsed.media_type,
            source="apify",
            caption=item.get("caption") or None,
            creator_username=item.get("ownerUsername") or owner.get("username"),
            thumbnail_url=item.get("displayUrl") or item.get("thumbnailUrl"),
            published_at=parse_timestamp(item.get("timestamp")),
            duration_sec=_to_float(item.get("videoDuration")),
            views=_first_not_none(to_count(item.get("videoPlayCount")), to_count(item.get("videoViewCount"))),
            likes=to_count(item.get("likesCount")),
            comments_count=to_count(item.get("commentsCount")),
            shares=_first_not_none(to_count(item.get("sharesCount")), to_count(item.get("reshareCount"))),
            saves=_first_not_none(to_count(item.get("savesCount")), to_count(item.get("saveCount"))),
            comments=self._normalize_comments(raw_comments),
        )

    # ------------------------------------------------------------------ Instaloader
    def _scrape_with_instaloader(self, parsed: InstagramURL) -> ScrapedPost:
        try:
            import instaloader
            from instaloader import exceptions as il_errors
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ScraperError("instaloader não instalado", transient=False, source="instaloader") from exc

        loader = self._instaloader_factory() if self._instaloader_factory else self._build_instaloader(instaloader)
        warnings: list[str] = []
        if self.settings.instagram_username and self.settings.instagram_sessionfile:
            try:
                loader.load_session_from_file(self.settings.instagram_username, self.settings.instagram_sessionfile)
            except (OSError, il_errors.InstaloaderException) as exc:
                warnings.append(f"Sessão do Instaloader não carregada ({type(exc).__name__}).")

        try:
            post = instaloader.Post.from_shortcode(loader.context, parsed.shortcode)
            # Touch a lazily-loaded attribute so metadata errors surface here.
            likes = post.likes
        except il_errors.QueryReturnedNotFoundException as exc:
            raise ScraperError("post não encontrado", transient=False, source="instaloader") from exc
        except (il_errors.LoginRequiredException, il_errors.PrivateProfileNotFollowedException) as exc:
            raise ScraperError("post privado ou exige login", transient=False, source="instaloader") from exc
        except (il_errors.TooManyRequestsException, il_errors.ConnectionException) as exc:
            raise ScraperError(f"Instagram recusou a conexão ({exc})", transient=True, source="instaloader") from exc
        except il_errors.InstaloaderException as exc:
            raise ScraperError(f"falha no Instaloader ({exc})", transient=True, source="instaloader") from exc

        raw_comments: list[dict[str, Any]] = []
        try:
            for comment in itertools.islice(post.get_comments(), self.settings.max_comments):
                raw_comments.append(
                    {
                        "id": comment.id,
                        "text": comment.text,
                        "ownerUsername": getattr(comment.owner, "username", None),
                        "likesCount": comment.likes_count,
                        "timestamp": comment.created_at_utc,
                    }
                )
        except il_errors.LoginRequiredException:
            warnings.append("Comentários exigem sessão autenticada (configure INSTAGRAM_SESSIONFILE).")
        except il_errors.InstaloaderException as exc:
            warnings.append(f"Coleta de comentários interrompida ({type(exc).__name__}).")

        is_video = bool(getattr(post, "is_video", False))
        views = None
        if is_video:
            views = _first_not_none(
                to_count(getattr(post, "video_play_count", None)), to_count(getattr(post, "video_view_count", None))
            )
        return ScrapedPost(
            instagram_id=parsed.shortcode,
            url=parsed.canonical_url,
            media_type=parsed.media_type,
            source="instaloader",
            caption=post.caption,
            creator_username=post.owner_username,
            thumbnail_url=post.url,
            published_at=parse_timestamp(post.date_utc),
            duration_sec=_to_float(getattr(post, "video_duration", None)) if is_video else None,
            views=views,
            likes=to_count(likes),
            comments_count=to_count(post.comments),
            comments=self._normalize_comments(raw_comments),
            warnings=warnings,
        )

    def _build_instaloader(self, instaloader: Any) -> Any:
        class BoundedRateController(instaloader.RateController):
            """Instaloader sleeps for minutes when rate limited; fail fast instead."""

            def sleep(self, secs: float) -> None:
                if secs > 10:
                    raise instaloader.exceptions.TooManyRequestsException(f"rate limited ({secs:.0f}s)")
                super().sleep(secs)

        return instaloader.Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            max_connection_attempts=1,
            request_timeout=30.0,
            rate_controller=lambda context: BoundedRateController(context),
        )

    # ------------------------------------------------------------------ mock
    def _scrape_mock(self, parsed: InstagramURL) -> ScrapedPost:
        from app.analyzer.services.mock_data import generate_mock_post

        return generate_mock_post(parsed, max_comments=self.settings.max_comments)

    # ------------------------------------------------------------------ normalization
    def _normalize_comments(self, raw_comments: Iterable[dict[str, Any]]) -> list[ScrapedComment]:
        """Deduplicate, clean and cap comments coming from any provider."""
        comments: list[ScrapedComment] = []
        seen: set[str] = set()
        for raw in raw_comments:
            if not isinstance(raw, dict):
                continue
            text = " ".join(str(raw.get("text") or "").split())[:MAX_COMMENT_LENGTH]
            if not text:
                continue
            raw_owner = raw.get("owner")
            owner: dict[str, Any] = raw_owner if isinstance(raw_owner, dict) else {}
            author = raw.get("ownerUsername") or owner.get("username") or raw.get("username")
            timestamp = raw.get("timestamp") or raw.get("created_at")
            comment_id = str(raw.get("id") or raw.get("pk") or "").strip()
            if not comment_id:
                digest = hashlib.sha1(f"{author}|{text}|{timestamp}".encode()).hexdigest()[:24]
                comment_id = f"h_{digest}"
            if comment_id in seen:
                continue
            seen.add(comment_id)
            comments.append(
                ScrapedComment(
                    comment_id=comment_id[:64],
                    text=text,
                    author=str(author)[:128] if author else None,
                    likes=to_count(_first_not_none(raw.get("likesCount"), raw.get("likes_count"), raw.get("likes")))
                    or 0,
                    created_at=parse_timestamp(timestamp),
                )
            )
            if len(comments) >= self.settings.max_comments:
                break
        return comments


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
