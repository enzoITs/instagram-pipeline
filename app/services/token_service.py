"""Keeps long-lived access tokens alive before they expire."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import TokenDecryptionError, decrypt_token, encrypt_token
from app.instagram.client import GraphAPIError, InstagramClient
from app.instagram.oauth import OAuthError, refresh_long_lived_token
from app.models import Account, utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshResult:
    account_id: int
    username: str | None
    refreshed: bool
    expires_at: datetime | None = None
    error: str | None = None


def token_needs_refresh(account: Account, settings: Settings | None = None) -> bool:
    """True when the account's token is within the refresh window.

    Meta only allows refreshing a token that is at least 24 hours old and not
    yet expired, so tokens without a known expiry are left alone.
    """
    settings = settings or get_settings()
    if not account.token_expires_at:
        return False
    expires_at = _as_utc(account.token_expires_at)
    threshold = datetime.now(timezone.utc) + timedelta(
        days=settings.token_refresh_threshold_days
    )
    return expires_at <= threshold


def refresh_account_token(
    db: Session,
    account: Account,
    *,
    client: InstagramClient | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> RefreshResult:
    """Refresh one account's token when it is close to expiring."""
    settings = settings or get_settings()
    if not force and not token_needs_refresh(account, settings):
        return RefreshResult(account.id, account.username, refreshed=False)

    try:
        current = decrypt_token(account.access_token_encrypted)
    except TokenDecryptionError as exc:
        return RefreshResult(account.id, account.username, False, error=str(exc))

    owns_client = client is None
    client = client or InstagramClient(settings)
    try:
        new_token, expires_at = refresh_long_lived_token(
            current, settings=settings, client=client
        )
    except (GraphAPIError, OAuthError) as exc:
        message = str(exc)
        if isinstance(exc, GraphAPIError) and exc.is_token_error:
            account.is_active = False
            db.add(account)
            db.commit()
            message = (
                f"Token refresh rejected by Meta ({exc}). The account was marked "
                "inactive — reconnect it from the dashboard."
            )
        logger.warning("Token refresh failed for account %s: %s", account.id, message)
        return RefreshResult(account.id, account.username, False, error=message)
    finally:
        if owns_client:
            client.close()

    account.access_token_encrypted = encrypt_token(new_token)
    account.token_refreshed_at = utcnow()
    if expires_at:
        account.token_expires_at = expires_at
    db.add(account)
    db.commit()

    logger.info("Refreshed token for account %s (expires %s).", account.id, expires_at)
    return RefreshResult(account.id, account.username, True, expires_at=expires_at)


def refresh_expiring_tokens(
    db: Session, *, settings: Settings | None = None
) -> list[RefreshResult]:
    """Refresh every active account whose token is near expiry."""
    settings = settings or get_settings()
    accounts = db.scalars(select(Account).where(Account.is_active.is_(True))).all()
    due = [a for a in accounts if token_needs_refresh(a, settings)]
    if not due:
        logger.debug("No tokens due for refresh.")
        return []

    results: list[RefreshResult] = []
    with InstagramClient(settings) as client:
        for account in due:
            results.append(
                refresh_account_token(db, account, client=client, settings=settings)
            )
    return results


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on read; treat naive datetimes as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
