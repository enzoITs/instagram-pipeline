"""The collection pipeline: list media, fetch insights, persist snapshots."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import TokenDecryptionError, decrypt_token
from app.instagram.client import GraphAPIError, InstagramClient
from app.instagram.metrics import (
    MEDIA_FIELDS,
    compute_engagement_rate,
    metrics_for,
    parse_insights,
    total_interactions,
)
from app.instagram.oauth import fetch_profile
from app.models import Account, CollectionRun, Media, MetricSnapshot, RunStatus, utcnow

logger = logging.getLogger(__name__)


class CollectionError(RuntimeError):
    """Raised when a collection run cannot proceed at all."""


@dataclass(slots=True)
class CollectionResult:
    """Summary of one account's collection run."""

    run_id: int
    account_id: int
    status: RunStatus
    media_seen: int
    media_created: int
    snapshots_created: int
    api_calls: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (RunStatus.SUCCESS, RunStatus.PARTIAL)


# --------------------------------------------------------------------- public
def collect_account(
    db: Session,
    account: Account,
    *,
    client: InstagramClient | None = None,
    settings: Settings | None = None,
    trigger: str = "manual",
    limit: int | None = None,
) -> CollectionResult:
    """Collect metrics for a single account and persist a snapshot per media."""
    settings = settings or get_settings()
    owns_client = client is None
    client = client or InstagramClient(settings)
    calls_before = client.call_count

    run = CollectionRun(account_id=account.id, trigger=trigger, status=RunStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)

    # One timestamp for the whole run keeps every snapshot aligned on the
    # dashboard's time axis and makes the run idempotent via the unique index.
    collected_at = datetime.now(timezone.utc)
    media_seen = media_created = snapshots_created = 0
    failures: list[str] = []

    try:
        token = decrypt_token(account.access_token_encrypted)
    except TokenDecryptionError as exc:
        return _finish(db, run, RunStatus.FAILED, 0, 0, 0, 0, str(exc), account_id=account.id)

    try:
        _refresh_profile(db, account, token, client=client, settings=settings)

        max_items = limit or settings.max_media_per_collection
        items = client.get_paginated(
            f"/{account.ig_user_id}/media",
            access_token=token,
            params={"fields": ",".join(MEDIA_FIELDS), "limit": min(max_items, 100)},
            limit=max_items,
        )
    except GraphAPIError as exc:
        if exc.is_token_error:
            account.is_active = False
            db.add(account)
            db.commit()
            message = (
                f"Access token rejected by Meta ({exc}). The account was marked "
                "inactive — reconnect it from the dashboard."
            )
        else:
            message = f"Could not list media: {exc}"
        calls = client.call_count - calls_before
        if owns_client:
            client.close()
        return _finish(db, run, RunStatus.FAILED, 0, 0, 0, calls, message, account_id=account.id)

    try:
        for item in items:
            media_seen += 1
            media, created = _upsert_media(db, account, item)
            if created:
                media_created += 1

            try:
                insights = _fetch_insights(client, token, item)
            except GraphAPIError as exc:
                if exc.is_token_error:
                    raise
                failures.append(f"{media.ig_media_id}: {exc}")
                insights = {}

            metrics = _merge_metrics(item, insights)
            rate, basis = compute_engagement_rate(metrics, account.followers_count)

            snapshot = MetricSnapshot(
                media_id=media.id,
                collected_at=collected_at,
                likes=metrics.get("likes"),
                comments=metrics.get("comments"),
                saved=metrics.get("saved"),
                shares=metrics.get("shares"),
                reach=metrics.get("reach"),
                views=metrics.get("views"),
                total_interactions=total_interactions(metrics),
                profile_visits=metrics.get("profile_visits"),
                follows=metrics.get("follows"),
                engagement_rate=rate,
                engagement_basis=basis,
                raw={"media": item, "insights": insights},
            )
            db.add(snapshot)

            media.last_snapshot_at = collected_at
            media.latest_engagement_rate = rate
            db.add(media)
            snapshots_created += 1

        account.last_collected_at = collected_at
        db.add(account)
        db.commit()

    except GraphAPIError as exc:
        db.rollback()
        if exc.is_token_error:
            account.is_active = False
            db.add(account)
            db.commit()
        calls = client.call_count - calls_before
        if owns_client:
            client.close()
        return _finish(
            db, run, RunStatus.FAILED, media_seen, media_created, 0, calls,
            f"Collection aborted: {exc}", account_id=account.id,
        )
    except Exception as exc:  # pragma: no cover - unexpected, still recorded
        db.rollback()
        logger.exception("Unexpected error during collection for account %s", account.id)
        calls = client.call_count - calls_before
        if owns_client:
            client.close()
        return _finish(
            db, run, RunStatus.FAILED, media_seen, media_created, 0, calls,
            f"Unexpected error: {exc}", account_id=account.id,
        )

    calls = client.call_count - calls_before
    if owns_client:
        client.close()

    status = RunStatus.PARTIAL if failures else RunStatus.SUCCESS
    error = "; ".join(failures[:10]) if failures else None
    return _finish(
        db, run, status, media_seen, media_created, snapshots_created, calls, error,
        account_id=account.id,
    )


def collect_all_accounts(
    db: Session,
    *,
    settings: Settings | None = None,
    trigger: str = "scheduled",
) -> list[CollectionResult]:
    """Run the collection for every active account, sharing one HTTP client."""
    settings = settings or get_settings()
    accounts = db.scalars(select(Account).where(Account.is_active.is_(True))).all()
    if not accounts:
        logger.info("No active accounts connected; nothing to collect.")
        return []

    results: list[CollectionResult] = []
    with InstagramClient(settings) as client:
        for account in accounts:
            logger.info("Collecting metrics for @%s", account.username or account.ig_user_id)
            results.append(
                collect_account(
                    db, account, client=client, settings=settings, trigger=trigger
                )
            )
    return results


# ------------------------------------------------------------------ internals
def _refresh_profile(
    db: Session,
    account: Account,
    token: str,
    *,
    client: InstagramClient,
    settings: Settings,
) -> None:
    """Keep follower counts current — they are the engagement-rate fallback."""
    try:
        profile = fetch_profile(
            token, settings=settings, client=client, ig_user_id=account.ig_user_id
        )
    except GraphAPIError as exc:
        if exc.is_token_error:
            raise
        logger.warning("Could not refresh profile for account %s: %s", account.id, exc)
        return

    account.username = profile.get("username") or account.username
    account.name = profile.get("name") or account.name
    account.account_type = profile.get("account_type") or account.account_type
    account.profile_picture_url = (
        profile.get("profile_picture_url") or account.profile_picture_url
    )
    if isinstance(profile.get("followers_count"), int):
        account.followers_count = profile["followers_count"]
    if isinstance(profile.get("media_count"), int):
        account.media_count = profile["media_count"]
    db.add(account)
    db.commit()


def _upsert_media(db: Session, account: Account, item: dict[str, Any]) -> tuple[Media, bool]:
    """Insert or update the Media row for one API item."""
    ig_media_id = str(item["id"])
    media = db.scalar(select(Media).where(Media.ig_media_id == ig_media_id))
    created = media is None
    if media is None:
        media = Media(account_id=account.id, ig_media_id=ig_media_id)

    media.media_type = item.get("media_type") or media.media_type
    media.media_product_type = item.get("media_product_type") or media.media_product_type
    media.caption = item.get("caption")
    media.permalink = item.get("permalink") or media.permalink
    media.media_url = item.get("media_url") or media.media_url
    media.thumbnail_url = item.get("thumbnail_url") or media.thumbnail_url
    media.timestamp = _parse_timestamp(item.get("timestamp")) or media.timestamp

    db.add(media)
    db.flush()  # assigns media.id for new rows
    return media, created


def _fetch_insights(
    client: InstagramClient, token: str, item: dict[str, Any]
) -> dict[str, int]:
    """Fetch insights, dropping metrics this media type does not support."""
    wanted = list(metrics_for(item.get("media_product_type")))
    if not wanted:
        return {}

    for _ in range(3):
        try:
            payload = client.get(
                f"/{item['id']}/insights",
                access_token=token,
                params={"metric": ",".join(wanted)},
            )
        except GraphAPIError as exc:
            if not exc.is_unsupported_metric:
                raise
            offending = exc.unsupported_metrics(tuple(wanted))
            remaining = [m for m in wanted if m not in offending]
            if not offending or not remaining:
                logger.info(
                    "No supported insight metrics for media %s (%s): %s",
                    item.get("id"),
                    item.get("media_product_type"),
                    exc.message,
                )
                return {}
            logger.info(
                "Dropping unsupported metrics %s for media %s and retrying.",
                sorted(offending),
                item.get("id"),
            )
            wanted = remaining
            continue
        return parse_insights(payload)

    return {}


def _merge_metrics(item: dict[str, Any], insights: dict[str, int]) -> dict[str, int | None]:
    """Combine node counters with insight values into one flat dict."""
    metrics: dict[str, int | None] = dict(insights)
    if isinstance(item.get("like_count"), int):
        metrics["likes"] = item["like_count"]
    if isinstance(item.get("comments_count"), int):
        metrics["comments"] = item["comments_count"]
    return metrics


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse Meta's ISO-8601 timestamps (``2025-01-31T12:00:00+0000``)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    # Normalise "+0000" to "+00:00" for fromisoformat on all versions.
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse timestamp %r from Meta.", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finish(
    db: Session,
    run: CollectionRun,
    status: RunStatus,
    media_seen: int,
    media_created: int,
    snapshots_created: int,
    api_calls: int,
    error: str | None,
    *,
    account_id: int,
) -> CollectionResult:
    run.status = status
    run.finished_at = utcnow()
    run.media_seen = media_seen
    run.media_created = media_created
    run.snapshots_created = snapshots_created
    run.api_calls = api_calls
    run.error = error
    db.add(run)
    db.commit()
    db.refresh(run)

    if error:
        logger.warning("Collection run %s finished as %s: %s", run.id, status.value, error)
    else:
        logger.info(
            "Collection run %s finished as %s (%s media, %s snapshots, %s API calls).",
            run.id, status.value, media_seen, snapshots_created, api_calls,
        )

    return CollectionResult(
        run_id=run.id,
        account_id=account_id,
        status=status,
        media_seen=media_seen,
        media_created=media_created,
        snapshots_created=snapshots_created,
        api_calls=api_calls,
        error=error,
    )
