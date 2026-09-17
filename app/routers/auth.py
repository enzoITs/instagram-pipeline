"""OAuth endpoints: start the Meta login and handle the callback."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import encrypt_token
from app.database import get_db
from app.instagram.client import GraphAPIError
from app.instagram.oauth import (
    STATE_COOKIE_NAME,
    STATE_MAX_AGE_SECONDS,
    OAuthError,
    build_authorization_url,
    exchange_code,
    make_state,
    new_nonce,
    verify_state,
)
from app.models import Account, utcnow
from app.schemas import AccountOut, LoginRequest, SessionOut
from app.security import (
    clear_session,
    issue_session,
    password_matches,
    require_session,
    session_is_valid,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _start_login(response: Response) -> str:
    """Mint a browser-bound authorisation URL and set the matching cookie."""
    settings = get_settings()
    nonce = new_nonce()
    try:
        url = build_authorization_url(settings, state=make_state(settings, nonce=nonce))
    except OAuthError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    response.set_cookie(
        STATE_COOKIE_NAME,
        nonce,
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https://"),
        path="/auth",
    )
    return url


@router.get("/session", response_model=SessionOut, summary="Current login state")
def read_session(request: Request) -> SessionOut:
    settings = get_settings()
    return SessionOut(
        auth_required=settings.auth_enabled,
        authenticated=session_is_valid(request, settings),
    )


@router.post("/session", response_model=SessionOut, summary="Sign in to the dashboard")
def create_session(payload: LoginRequest, response: Response) -> SessionOut:
    settings = get_settings()
    if not settings.auth_enabled:
        # Nothing to sign in to; say so rather than pretending it worked.
        return SessionOut(auth_required=False, authenticated=True)
    if not password_matches(payload.password, settings):
        logger.warning("Rejected a dashboard sign-in attempt.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password.")
    issue_session(response, settings)
    return SessionOut(auth_required=True, authenticated=True)


@router.delete("/session", response_model=SessionOut, summary="Sign out")
def delete_session(response: Response) -> SessionOut:
    clear_session(response)
    return SessionOut(auth_required=get_settings().auth_enabled, authenticated=False)


@router.get(
    "/login",
    summary="Redirect the user to Meta's authorisation screen",
    dependencies=[Depends(require_session)],
)
def login() -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    url = _start_login(response)
    response.headers["location"] = url
    return response


@router.get(
    "/login-url",
    summary="Return the authorisation URL as JSON",
    dependencies=[Depends(require_session)],
)
def login_url(response: Response) -> dict[str, str]:
    return {"url": _start_login(response)}


@router.get("/callback", summary="OAuth redirect target — do not call directly")
def callback(
    request: Request,
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
        verify_state(
            state, settings, expected_nonce=request.cookies.get(STATE_COOKIE_NAME)
        )
        connected = exchange_code(code, settings=settings)
    except (OAuthError, GraphAPIError) as exc:
        logger.warning("OAuth callback failed: %s", exc)
        return _clear_state_cookie(_back_to_dashboard(f"error={_quote(str(exc))}"))

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
    # A lean profile response (Meta drops fields it cannot serve) must not wipe
    # the follower history already collected for this account.
    if connected.followers_count is not None:
        account.followers_count = connected.followers_count
    if connected.media_count is not None:
        account.media_count = connected.media_count
    account.facebook_page_id = connected.facebook_page_id or account.facebook_page_id
    account.is_active = True

    db.add(account)
    db.commit()
    db.refresh(account)

    logger.info("Connected Instagram account @%s (id=%s).", account.username, account.id)
    return _clear_state_cookie(_back_to_dashboard(f"connected={account.id}"))


@router.delete(
    "/accounts/{account_id}",
    summary="Disconnect an account and delete its stored token",
    response_model=AccountOut,
    dependencies=[Depends(require_session)],
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


def _clear_state_cookie(response: RedirectResponse) -> RedirectResponse:
    """A nonce is single-use: drop it as soon as the handshake ends."""
    response.delete_cookie(STATE_COOKIE_NAME, path="/auth")
    return response


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:300])
