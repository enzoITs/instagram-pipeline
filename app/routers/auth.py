"""OAuth endpoints: start the Meta login and handle the callback."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import encrypt_token
from app.database import get_db
from app.instagram.client import GraphAPIError
from app.instagram.oauth import (
    OAuthError,
    build_authorization_url,
    exchange_code,
    make_state,
    verify_state,
)
from app.models import Account, utcnow
from app.schemas import AccountOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", summary="Redirect the user to Meta's authorisation screen")
def login() -> RedirectResponse:
    settings = get_settings()
    try:
        url = build_authorization_url(settings, state=make_state(settings))
    except OAuthError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/login-url", summary="Return the authorisation URL as JSON")
def login_url() -> dict[str, str]:
    settings = get_settings()
    try:
        return {"url": build_authorization_url(settings, state=make_state(settings))}
    except OAuthError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/callback", summary="OAuth redirect target — do not call directly")
def callback(
    db: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    error_reason: str | None = Query(default=None),
) -> RedirectResponse:
    settings = get_settings()

    if error:
        reason = error_description or error_reason or error
        logger.warning("Meta returned an OAuth error: %s", reason)
        return _back_to_dashboard(f"error={_quote(reason)}")

    if not code:
        return _back_to_dashboard("error=" + _quote("Meta did not return an authorization code."))

    if not state:
        return _back_to_dashboard("error=" + _quote("Missing OAuth state parameter."))

    try:
        verify_state(state, settings)
        connected = exchange_code(code, settings=settings)
    except (OAuthError, GraphAPIError) as exc:
        logger.warning("OAuth callback failed: %s", exc)
        return _back_to_dashboard(f"error={_quote(str(exc))}")

    account = db.scalar(select(Account).where(Account.ig_user_id == connected.ig_user_id))
    if account is None:
        account = Account(ig_user_id=connected.ig_user_id)

    account.access_token_encrypted = encrypt_token(connected.access_token)
    account.token_expires_at = connected.expires_at
    account.token_refreshed_at = utcnow()
    account.username = connected.username or account.username
    account.name = connected.name or account.name
    account.account_type = connected.account_type or account.account_type
    account.profile_picture_url = connected.profile_picture_url or account.profile_picture_url
    account.followers_count = connected.followers_count
    account.media_count = connected.media_count
    account.facebook_page_id = connected.facebook_page_id or account.facebook_page_id
    account.is_active = True

    db.add(account)
    db.commit()
    db.refresh(account)

    logger.info("Connected Instagram account @%s (id=%s).", account.username, account.id)
    return _back_to_dashboard(f"connected={account.id}")


@router.delete(
    "/accounts/{account_id}",
    summary="Disconnect an account and delete its stored token",
    response_model=AccountOut,
)
def disconnect(account_id: int, db: Session = Depends(get_db)) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    account.is_active = False
    account.access_token_encrypted = ""
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _back_to_dashboard(query: str) -> RedirectResponse:
    return RedirectResponse(f"/?{query}", status_code=status.HTTP_303_SEE_OTHER)


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:300])
