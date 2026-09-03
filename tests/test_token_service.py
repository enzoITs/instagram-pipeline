"""Tests for long-lived token refresh scheduling and persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import respx

from app.crypto import decrypt_token, encrypt_token
from app.services.token_service import (
    refresh_account_token,
    refresh_expiring_tokens,
    token_needs_refresh,
)

REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


class TestNeedsRefresh:
    def test_token_far_from_expiry_is_left_alone(self, account, settings) -> None:
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(days=55)
        assert token_needs_refresh(account, settings) is False

    def test_token_inside_the_window_is_due(self, account, settings) -> None:
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        assert token_needs_refresh(account, settings) is True

    def test_naive_timestamps_from_sqlite_are_treated_as_utc(self, account, settings) -> None:
        account.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)
        assert token_needs_refresh(account, settings) is True

    def test_unknown_expiry_is_never_refreshed(self, account, settings) -> None:
        account.token_expires_at = None
        assert token_needs_refresh(account, settings) is False


class TestRefresh:
    @respx.mock
    def test_stores_the_new_token_encrypted(self, db, account, api_client) -> None:
        respx.get(REFRESH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "brand-new-token", "expires_in": 5_183_944}
            )
        )
        result = refresh_account_token(db, account, client=api_client, force=True)

        assert result.refreshed is True
        db.refresh(account)
        assert decrypt_token(account.access_token_encrypted) == "brand-new-token"
        assert account.token_expires_at > datetime.now(timezone.utc) + timedelta(days=55)
        assert account.token_refreshed_at is not None

    @respx.mock
    def test_a_rejected_token_deactivates_the_account(self, db, account, api_client) -> None:
        respx.get(REFRESH_URL).mock(
            return_value=httpx.Response(
                400, json={"error": {"message": "Invalid token", "code": 190}}
            )
        )
        result = refresh_account_token(db, account, client=api_client, force=True)

        assert result.refreshed is False
        assert "reconnect" in (result.error or "").lower()
        db.refresh(account)
        assert account.is_active is False

    def test_not_forced_and_not_due_is_a_no_op(self, db, account, api_client) -> None:
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(days=50)
        result = refresh_account_token(db, account, client=api_client)
        assert result.refreshed is False
        assert result.error is None

    def test_an_undecryptable_token_reports_an_error(self, db, account, api_client) -> None:
        account.access_token_encrypted = "corrupted"
        db.add(account)
        db.commit()
        result = refresh_account_token(db, account, client=api_client, force=True)
        assert result.refreshed is False
        assert "decrypted" in result.error

    @respx.mock
    def test_batch_refresh_only_touches_due_accounts(self, db, account) -> None:
        from app.models import Account

        due = Account(
            ig_user_id="17841400000000002",
            username="due.account",
            access_token_encrypted=encrypt_token("old"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=2),
            is_active=True,
        )
        db.add(due)
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(days=50)
        db.add(account)
        db.commit()

        respx.get(REFRESH_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 100})
        )
        results = refresh_expiring_tokens(db)

        assert [r.username for r in results] == ["due.account"]
