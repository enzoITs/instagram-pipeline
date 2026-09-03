"""Symmetric encryption for access tokens stored in the database."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenDecryptionError(RuntimeError):
    """Raised when a stored token cannot be decrypted with the current key."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key.strip()
    if key:
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError) as exc:  # pragma: no cover - config error
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc

    # No dedicated key configured: derive a deterministic one from SECRET_KEY so
    # the app still works out of the box during development.
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str) -> str:
    """Encrypt a plaintext access token for storage."""
    if not token:
        raise ValueError("Cannot encrypt an empty token.")
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token previously produced by :func:`encrypt_token`."""
    if not ciphertext:
        raise TokenDecryptionError("No token stored for this account.")
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError(
            "Stored token could not be decrypted. This usually means "
            "SECRET_KEY or TOKEN_ENCRYPTION_KEY changed after the account was "
            "connected — reconnect the account to fix it."
        ) from exc
