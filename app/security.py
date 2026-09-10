"""Password login for the dashboard and the API.

The app holds long-lived Instagram access tokens and can spend the Meta call
quota, so leaving every endpoint open is only defensible on a machine nobody
else can reach. Setting `ADMIN_PASSWORD` turns on a single-user session login;
leaving it empty keeps the previous open behaviour, with a startup warning.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "ig_session"
SESSION_SALT = "instagram-pipeline-session"


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=SESSION_SALT)


def password_matches(candidate: str, settings: Settings | None = None) -> bool:
    """Constant-time comparison against the configured password."""
    settings = settings or get_settings()
    if not settings.admin_password:
        return False
    return hmac.compare_digest(candidate, settings.admin_password)


def issue_session(response: Response, settings: Settings | None = None) -> None:
    """Attach a signed session cookie to *response*."""
    settings = settings or get_settings()
    token = _serializer(settings).dumps({"v": 1})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_max_age_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https://"),
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def session_is_valid(request: Request, settings: Settings | None = None) -> bool:
    """True when the request carries a signed, unexpired session cookie."""
    settings = settings or get_settings()
    if not settings.auth_enabled:
        return True
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    try:
        _serializer(settings).loads(
            token, max_age=settings.session_max_age_hours * 3600
        )
    except (BadSignature, SignatureExpired):
        return False
    return True


def require_session(request: Request) -> None:
    """FastAPI dependency guarding an endpoint behind the login."""
    if session_is_valid(request):
        return
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Authentication required. Sign in on the dashboard.",
        headers={"WWW-Authenticate": "Cookie"},
    )


# Routers apply this as a router-level dependency.
Protected: Any = Depends(require_session)
