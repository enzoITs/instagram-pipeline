"""Official metrics for a link that belongs to a connected account.

Scraping a public post can only see likes, comments and (for Reels) views.
The owner of the post can see reach, saves and shares through the Graph API —
the numbers this project already collects for connected accounts. So when an
analysed URL matches a post in the `media` table, the metrics come from Meta
instead of a scraper, and the comments too when the Graph permission for them
was granted. The scraper is still used for the comments otherwise, and for
everything when the post belongs to nobody here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.services.scraper_service import ScrapedComment, ScrapedPost
from app.config import Settings, get_settings
from app.crypto import TokenDecryptionError, decrypt_token
from app.instagram.client import GraphAPIError, InstagramClient
from app.instagram.metrics import MEDIA_FIELDS, metrics_for
from app.instagram.url import InstagramURL
from app.models import Account, Media

logger = logging.getLogger(__name__)

GRAPH_SOURCE = "graph_api"
COMMENT_FIELDS = ("id", "text", "username", "like_count", "timestamp")


def find_owned_media(db: Session, shortcode: str) -> Media | None:
    """The connected-account post this URL points at, if there is one.

    Meta does not expose a "shortcode" field, so the permalink it stores for
    every post is what ties the two halves of the product together.
    """
    return db.scalars(
        select(Media)
        .join(Account, Account.id == Media.account_id)
        .where(
            Account.is_active.is_(True),
            Account.access_token_encrypted != "",
            Media.permalink.like(f"%/{shortcode}/%"),
        )
        .order_by(Media.id.desc())
        .limit(1)
    ).first()


def load_owned_post(
    db: Session,
    parsed: InstagramURL,
    *,
    settings: Settings | None = None,
    client: InstagramClient | None = None,
) -> tuple[ScrapedPost, int] | None:
    """Return ``(post, media_id)`` built from the official API, or None.

    None means "not ours, or the API would not answer" — the caller then falls
    back to the scraper, which is the only option for someone else's post.
    """
    settings = settings or get_settings()
    media = find_owned_media(db, parsed.shortcode)
    if media is None:
        return None

    account = db.get(Account, media.account_id)
    if account is None:  # pragma: no cover - cascade would have removed the media
        return None
    try:
        token = decrypt_token(account.access_token_encrypted)
    except TokenDecryptionError as exc:
        logger.warning("graph_source_token_unreadable", extra={"account_id": account.id, "error": str(exc)})
        return None

    owns_client = client is None
    client = client or InstagramClient(settings)
    try:
        node = client.get(
            f"/{media.ig_media_id}",
            access_token=token,
            params={"fields": ",".join(MEDIA_FIELDS)},
        )
        insights = _insights(client, token, node, media)
        comments, warnings = _comments(client, token, media, settings)
    except GraphAPIError as exc:
        # A rejected token or a rate limit is not worth failing the analysis for:
        # the scraper can still produce a (less complete) result.
        logger.warning(
            "graph_source_unavailable",
            extra={"media_id": media.id, "error": str(exc), "token_error": exc.is_token_error},
        )
        return None
    finally:
        if owns_client:
            client.close()

    post = ScrapedPost(
        instagram_id=parsed.shortcode,
        url=parsed.canonical_url,
        media_type=parsed.media_type,
        source=GRAPH_SOURCE,
        caption=node.get("caption") or media.caption,
        creator_username=account.username,
        thumbnail_url=node.get("thumbnail_url") or node.get("media_url") or media.thumbnail_url,
        published_at=_timestamp(node.get("timestamp")) or media.timestamp,
        views=insights.get("views"),
        likes=_count(node.get("like_count")),
        comments_count=_count(node.get("comments_count")),
        shares=insights.get("shares"),
        saves=insights.get("saved"),
        reach=insights.get("reach"),
        followers_count=account.followers_count,
        comments=comments,
        warnings=warnings,
    )
    logger.info(
        "graph_source_used",
        extra={"media_id": media.id, "account_id": account.id, "comments": len(comments)},
    )
    return post, media.id


def merge_official_metrics(official: ScrapedPost, scraped: ScrapedPost) -> ScrapedPost:
    """Official numbers, scraped comments: the best of both sources."""
    merged = official
    merged.source = f"{official.source}+{scraped.source}"
    merged.caption = official.caption or scraped.caption
    merged.thumbnail_url = official.thumbnail_url or scraped.thumbnail_url
    merged.duration_sec = official.duration_sec or scraped.duration_sec
    merged.views = official.views if official.views is not None else scraped.views
    merged.likes = official.likes if official.likes is not None else scraped.likes
    merged.comments_count = (
        official.comments_count if official.comments_count is not None else scraped.comments_count
    )
    if not merged.comments:
        merged.comments = scraped.comments
    merged.warnings = [*official.warnings, *scraped.warnings]
    return merged


# --------------------------------------------------------------------- internals
def _insights(
    client: InstagramClient, token: str, node: dict[str, Any], media: Media
) -> dict[str, int]:
    """Insight metrics for this post, dropping the ones its format rejects."""
    from app.services.collector import fetch_media_insights

    item = {**node, "id": media.ig_media_id}
    item.setdefault("media_product_type", media.media_product_type)
    if not metrics_for(item.get("media_product_type")):
        return {}
    insights, _warning = fetch_media_insights(client, token, item)
    return insights


def _comments(
    client: InstagramClient, token: str, media: Media, settings: Settings
) -> tuple[list[ScrapedComment], list[str]]:
    """Comments straight from the API, when the permission was granted."""
    if not settings.enable_graph_comments:
        return [], []
    try:
        rows = client.get_paginated(
            f"/{media.ig_media_id}/comments",
            access_token=token,
            params={"fields": ",".join(COMMENT_FIELDS), "limit": 100},
            limit=settings.max_comments,
        )
    except GraphAPIError as exc:
        logger.warning("graph_comments_unavailable", extra={"media_id": media.id, "error": str(exc)})
        return [], [
            "Comentários pela API oficial indisponíveis "
            f"({exc}); usando a coleta pública."
        ]

    comments = [
        ScrapedComment(
            comment_id=str(row["id"])[:64],
            text=text,
            author=(row.get("username") or None),
            likes=_count(row.get("like_count")) or 0,
            created_at=_timestamp(row.get("timestamp")),
        )
        for row in rows
        if (text := " ".join(str(row.get("text") or "").split()))
    ]
    return comments, []


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _timestamp(value: Any) -> datetime | None:
    from app.services.collector import parse_graph_timestamp

    return parse_graph_timestamp(value)
