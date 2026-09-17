"""Tests for the dashboard login.

The suite as a whole runs with ADMIN_PASSWORD unset, which keeps the app open
— the documented behaviour for a machine nobody else can reach. These tests
turn the password on for the duration of a single test to exercise the
protected path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PROTECTED = [
    ("get", "/api/accounts"),
    ("get", "/api/runs"),
    ("get", "/api/media/1"),
    ("get", "/auth/login-url"),
    # The link analysis spends Apify/OpenAI credits and can delete results,
    # so it sits behind the same login.
    ("get", "/api/v1/recent"),
    ("post", "/api/v1/analyze"),
    ("delete", "/api/v1/analysis/00000000-0000-0000-0000-000000000000"),
]


@pytest.fixture()
def secured(db, fake_server):
    """A client for an app whose ADMIN_PASSWORD is set."""
    import fakeredis

    from app.analyzer.services.cache_service import CacheService
    from app.main import app

    settings = get_settings()
    settings.admin_password = "correct horse battery staple"
    try:
        with TestClient(app, base_url="https://testserver") as client:
            redis_client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
            app.state.redis = redis_client
            app.state.cache = CacheService(redis_client, settings.cache_ttl_seconds)
            yield client
    finally:
        settings.admin_password = ""


class TestOpenByDefault:
    def test_without_a_password_everything_stays_reachable(self, http_client) -> None:
        assert http_client.get("/api/accounts").status_code == 200

    def test_health_reports_that_no_login_is_required(self, http_client) -> None:
        body = http_client.get("/api/health").json()
        assert body["auth_required"] is False
        assert body["environment"] == "test"


class TestProtected:
    @pytest.mark.parametrize("method,path", PROTECTED)
    def test_anonymous_calls_are_rejected(self, secured, method, path) -> None:
        assert getattr(secured, method)(path).status_code == 401

    def test_anonymous_health_hides_the_configuration(self, secured) -> None:
        body = secured.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["auth_required"] is True
        # None of the configuration detail leaks to an anonymous caller.
        assert body["environment"] is None
        assert body["login_flow"] is None
        assert body["connected_accounts"] is None

    def test_a_wrong_password_is_refused(self, secured) -> None:
        response = secured.post("/auth/session", json={"password": "guess"})
        assert response.status_code == 401
        assert secured.get("/api/accounts").status_code == 401

    def test_signing_in_unlocks_the_api(self, secured) -> None:
        response = secured.post(
            "/auth/session", json={"password": "correct horse battery staple"}
        )
        assert response.status_code == 200
        assert response.json() == {"auth_required": True, "authenticated": True}

        assert secured.get("/api/accounts").status_code == 200
        health = secured.get("/api/health").json()
        assert health["environment"] == "test"

    def test_signing_out_locks_it_again(self, secured) -> None:
        secured.post("/auth/session", json={"password": "correct horse battery staple"})
        assert secured.get("/api/accounts").status_code == 200

        secured.delete("/auth/session")
        assert secured.get("/api/accounts").status_code == 401

    def test_a_forged_cookie_is_rejected(self, secured) -> None:
        secured.cookies.set("ig_session", "not-a-real-signed-value")
        assert secured.get("/api/accounts").status_code == 401

    def test_disconnecting_an_account_requires_a_session(self, secured, account) -> None:
        assert secured.delete(f"/auth/accounts/{account.id}").status_code == 401

    def test_collecting_requires_a_session(self, secured, account) -> None:
        assert secured.post(f"/api/accounts/{account.id}/collect").status_code == 401

    def test_the_csv_export_requires_a_session(self, secured, account) -> None:
        assert secured.get(f"/api/accounts/{account.id}/export.csv").status_code == 401


class TestLoginHardening:
    def test_guessing_the_password_is_rate_limited(self, secured) -> None:
        statuses = [
            secured.post("/auth/session", json={"password": f"guess-{n}"}).status_code
            for n in range(6)
        ]
        assert statuses == [401] * 5 + [429]
        # Even the right password waits for the window to pass.
        right = secured.post("/auth/session", json={"password": "correct horse battery staple"})
        assert right.status_code == 429
        assert right.headers["Retry-After"]

    def test_changing_the_password_ends_existing_sessions(self, secured) -> None:
        secured.post("/auth/session", json={"password": "correct horse battery staple"})
        assert secured.get("/api/accounts").status_code == 200

        settings = get_settings()
        settings.admin_password = "a brand new password"
        assert secured.get("/api/accounts").status_code == 401
        assert secured.get("/auth/session").json() == {"auth_required": True, "authenticated": False}
