"""OAuth 2.0 flows for connecting an Instagram Business/Creator account.

Two flows are supported, selected through ``META_LOGIN_FLOW``:

``instagram_login``
    "Instagram API with Instagram Login". The user signs in with Instagram
    directly; no Facebook Page is required. This is the default and the
    simplest path for a creator managing their own account.

``facebook_login``
    "Instagram API with Facebook Login". The user signs in with Facebook and
    the app reads the Instagram account linked to one of their Pages. Required
    for agency setups that also need Page-level data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings
from app.instagram.client import GraphAPIError, InstagramClient
from app.instagram.metrics import ACCOUNT_FIELDS

logger = logging.getLogger(__name__)

INSTAGRAM_AUTH_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_GRAPH_HOST = "https://graph.instagram.com"
FACEBOOK_GRAPH_HOST = "https://graph.facebook.com"

STATE_SALT = "instagram-pipeline-oauth-state"
STATE_MAX_AGE_SECONDS = 600


class OAuthError(RuntimeError):
    """Raised when the OAuth handshake cannot be completed."""


@dataclass(slots=True)
class ConnectedAccount:
    """Everything needed to persist a freshly connected account."""

    ig_user_id: str
    access_token: str
    expires_at: datetime | None
    username: str | None = None
    name: str | None = None
    account_type: str | None = None
    profile_picture_url: str | None = None
    followers_count: int | None = None
    media_count: int | None = None
    facebook_page_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- state
def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=STATE_SALT)


def make_state(settings: Settings | None = None, payload: dict[str, Any] | None = None) -> str:
    """Create a signed, time-limited CSRF `state` value."""
    settings = settings or get_settings()
    return _serializer(settings).dumps(payload or {"v": 1})


def verify_state(state: str, settings: Settings | None = None) -> dict[str, Any]:
    """Validate a `state` value returned by Meta. Raises :class:`OAuthError`."""
    settings = settings or get_settings()
    try:
        return _serializer(settings).loads(state, max_age=STATE_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise OAuthError("The login link expired. Please start the connection again.") from exc
    except BadSignature as exc:
        raise OAuthError("Invalid OAuth state — the request may have been tampered with.") from exc


# ------------------------------------------------------------- authorize URL
def build_authorization_url(settings: Settings | None = None, state: str | None = None) -> str:
    """Return the URL the user must visit to authorise the app."""
    settings = settings or get_settings()
    if not settings.is_configured:
        raise OAuthError(
            "INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET are not set. "
            "See SETUP.md for how to create the Meta app and fill in the .env file."
        )

    state = state or make_state(settings)

    if settings.meta_login_flow == "facebook_login":
        params = {
            "client_id": settings.instagram_app_id,
            "redirect_uri": settings.redirect_uri,
            "response_type": "code",
            "scope": settings.scopes,
            "state": state,
        }
        base = f"https://www.facebook.com/{settings.graph_api_version}/dialog/oauth"
        return f"{base}?{urlencode(params)}"

    params = {
        "client_id": settings.instagram_app_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": settings.scopes,
        "state": state,
    }
    return f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"


# ------------------------------------------------------------ code -> account
def exchange_code(
    code: str,
    *,
    settings: Settings | None = None,
    client: InstagramClient | None = None,
) -> ConnectedAccount:
    """Turn an authorization `code` into a stored-ready long-lived token."""
    settings = settings or get_settings()
    owns_client = client is None
    client = client or InstagramClient(settings)
    try:
        if settings.meta_login_flow == "facebook_login":
            return _exchange_code_facebook(code, settings, client)
        return _exchange_code_instagram(code, settings, client)
    finally:
        if owns_client:
            client.close()


def _exchange_code_instagram(
    code: str, settings: Settings, client: InstagramClient
) -> ConnectedAccount:
    # Instagram appends "#_" to the code in the browser redirect.
    code = code.split("#")[0]

    short = client.post(
        INSTAGRAM_TOKEN_URL,
        data={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": settings.redirect_uri,
            "code": code,
        },
        versioned=False,
    )

    # The endpoint returns either a flat object or {"data": [ {...} ]}.
    if isinstance(short.get("data"), list) and short["data"]:
        short = short["data"][0]

    short_token = short.get("access_token")
    if not short_token:
        raise OAuthError(f"Meta did not return an access token: {short}")

    long_lived = client.get(
        "/access_token",
        access_token=short_token,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_app_secret,
        },
        base_url=INSTAGRAM_GRAPH_HOST,
        versioned=False,
    )
    token = long_lived.get("access_token")
    if not token:
        raise OAuthError(f"Could not exchange for a long-lived token: {long_lived}")

    expires_at = _expiry_from(long_lived.get("expires_in"))
    profile = fetch_profile(token, settings=settings, client=client)

    ig_user_id = str(profile.get("id") or short.get("user_id") or "")
    if not ig_user_id:
        raise OAuthError("Meta did not return an Instagram user id.")

    return ConnectedAccount(
        ig_user_id=ig_user_id,
        access_token=token,
        expires_at=expires_at,
        username=profile.get("username"),
        name=profile.get("name"),
        account_type=profile.get("account_type"),
        profile_picture_url=profile.get("profile_picture_url"),
        followers_count=profile.get("followers_count"),
        media_count=profile.get("media_count"),
        raw=profile,
    )


def _exchange_code_facebook(
    code: str, settings: Settings, client: InstagramClient
) -> ConnectedAccount:
    short = client.get(
        "/oauth/access_token",
        access_token="",  # not required here; credentials go in the query
        params={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "redirect_uri": settings.redirect_uri,
            "code": code,
        },
        base_url=FACEBOOK_GRAPH_HOST,
    )
    short_token = short.get("access_token")
    if not short_token:
        raise OAuthError(f"Meta did not return an access token: {short}")

    long_lived = client.get(
        "/oauth/access_token",
        access_token="",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "fb_exchange_token": short_token,
        },
        base_url=FACEBOOK_GRAPH_HOST,
    )
    token = long_lived.get("access_token") or short_token
    expires_at = _expiry_from(long_lived.get("expires_in"))

    pages = client.get(
        "/me/accounts",
        access_token=token,
        params={"fields": "id,name,access_token,instagram_business_account{id,username}"},
        base_url=FACEBOOK_GRAPH_HOST,
    )
    for page in pages.get("data") or []:
        ig_account = page.get("instagram_business_account")
        if not ig_account:
            continue
        page_token = page.get("access_token") or token
        profile = fetch_profile(page_token, settings=settings, client=client, ig_user_id=ig_account["id"])
        return ConnectedAccount(
            ig_user_id=str(ig_account["id"]),
            access_token=page_token,
            # Page tokens derived from a long-lived user token do not expire.
            expires_at=expires_at,
            username=profile.get("username") or ig_account.get("username"),
            name=profile.get("name"),
            account_type=profile.get("account_type"),
            profile_picture_url=profile.get("profile_picture_url"),
            followers_count=profile.get("followers_count"),
            media_count=profile.get("media_count"),
            facebook_page_id=str(page.get("id")),
            raw=profile,
        )

    raise OAuthError(
        "No Instagram Business account is linked to any of your Facebook Pages. "
        "Link the account in Meta Business Suite and try again."
    )


# ------------------------------------------------------------------- profile
def fetch_profile(
    access_token: str,
    *,
    settings: Settings | None = None,
    client: InstagramClient | None = None,
    ig_user_id: str | None = None,
) -> dict[str, Any]:
    """Read the connected account's profile fields."""
    settings = settings or get_settings()
    owns_client = client is None
    client = client or InstagramClient(settings)
    node = ig_user_id or "me"
    fields = ",".join(ACCOUNT_FIELDS)
    # The host is taken from *these* settings rather than the client's, so the
    # Facebook flow reads the profile from graph.facebook.com even when the
    # client was built for a different default.
    host = settings.graph_host
    try:
        return client.get(
            f"/{node}", access_token=access_token, params={"fields": fields}, base_url=host
        )
    except GraphAPIError as exc:
        # `followers_count` is unavailable on some account types; retry leaner.
        if exc.code == 100:
            reduced = "id,username,account_type"
            return client.get(
                f"/{node}", access_token=access_token, params={"fields": reduced}, base_url=host
            )
        raise
    finally:
        if owns_client:
            client.close()


# -------------------------------------------------------------------- refresh
def refresh_long_lived_token(
    access_token: str,
    *,
    settings: Settings | None = None,
    client: InstagramClient | None = None,
) -> tuple[str, datetime | None]:
    """Extend a long-lived token for another 60 days.

    Only the Instagram Login flow exposes a refresh endpoint. Facebook Page
    tokens derived from a long-lived user token do not expire, so the current
    token is returned unchanged.
    """
    settings = settings or get_settings()
    if settings.meta_login_flow == "facebook_login":
        return access_token, None

    owns_client = client is None
    client = client or InstagramClient(settings)
    try:
        payload = client.get(
            "/refresh_access_token",
            access_token=access_token,
            params={"grant_type": "ig_refresh_token"},
            base_url=INSTAGRAM_GRAPH_HOST,
            versioned=False,
        )
    finally:
        if owns_client:
            client.close()

    token = payload.get("access_token")
    if not token:
        raise OAuthError(f"Token refresh did not return a new token: {payload}")
    return token, _expiry_from(payload.get("expires_in"))


def _expiry_from(expires_in: Any) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
