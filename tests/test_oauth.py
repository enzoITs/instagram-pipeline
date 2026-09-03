"""Tests for the OAuth handshake and token refresh."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.config import Settings
from app.instagram.oauth import (
    OAuthError,
    build_authorization_url,
    exchange_code,
    make_state,
    refresh_long_lived_token,
    verify_state,
)


class TestAuthorizationUrl:
    def test_instagram_flow_targets_instagram_com(self, settings) -> None:
        url = build_authorization_url(settings, state="the-state")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        assert parsed.netloc == "www.instagram.com"
        assert query["client_id"] == ["test-app-id"]
        assert query["redirect_uri"] == ["https://example.test/auth/callback"]
        assert query["response_type"] == ["code"]
        assert query["state"] == ["the-state"]
        assert "instagram_business_manage_insights" in query["scope"][0]

    def test_facebook_flow_targets_facebook_com(self) -> None:
        settings = Settings(
            instagram_app_id="id",
            instagram_app_secret="secret",
            meta_login_flow="facebook_login",
            public_base_url="https://example.test",
        )
        url = build_authorization_url(settings, state="s")
        assert url.startswith("https://www.facebook.com/v23.0/dialog/oauth")
        assert "instagram_manage_insights" in url

    def test_missing_credentials_raise_a_helpful_error(self) -> None:
        settings = Settings(instagram_app_id="", instagram_app_secret="")
        with pytest.raises(OAuthError, match="SETUP.md"):
            build_authorization_url(settings)


class TestState:
    def test_round_trip(self, settings) -> None:
        assert verify_state(make_state(settings), settings) == {"v": 1}

    def test_tampered_state_is_rejected(self, settings) -> None:
        with pytest.raises(OAuthError, match="Invalid OAuth state"):
            verify_state(make_state(settings) + "x", settings)

    def test_state_signed_with_another_key_is_rejected(self, settings) -> None:
        other = Settings(secret_key="a-completely-different-secret-key-value")
        with pytest.raises(OAuthError):
            verify_state(make_state(other), settings)


class TestExchangeCode:
    @respx.mock
    def test_full_instagram_handshake(self, settings, api_client) -> None:
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(
                200, json={"data": [{"access_token": "short-lived", "user_id": 17841400000000001}]}
            )
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "long-lived", "expires_in": 5_183_944}
            )
        )
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "17841400000000001",
                    "username": "test.account",
                    "account_type": "BUSINESS",
                    "followers_count": 12345,
                    "media_count": 87,
                },
            )
        )

        account = exchange_code("the-code#_", settings=settings, client=api_client)

        assert account.ig_user_id == "17841400000000001"
        assert account.access_token == "long-lived"
        assert account.username == "test.account"
        assert account.followers_count == 12345
        assert account.expires_at is not None

    @respx.mock
    def test_the_url_fragment_is_stripped_from_the_code(self, settings, api_client) -> None:
        route = respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "s", "user_id": 1})
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "l", "expires_in": 100})
        )
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(200, json={"id": "1", "username": "u"})
        )

        exchange_code("abc123#_", settings=settings, client=api_client)

        body = dict(parse_qs(route.calls[0].request.content.decode()))
        assert body["code"] == ["abc123"]

    @respx.mock
    def test_flat_token_response_shape_is_supported(self, settings, api_client) -> None:
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "short", "user_id": 99})
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "long", "expires_in": 60})
        )
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            return_value=httpx.Response(200, json={"id": "99", "username": "flat"})
        )
        account = exchange_code("code", settings=settings, client=api_client)
        assert account.ig_user_id == "99"

    @respx.mock
    def test_missing_token_raises_oauth_error(self, settings, api_client) -> None:
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"data": [{}]})
        )
        with pytest.raises(OAuthError, match="did not return an access token"):
            exchange_code("code", settings=settings, client=api_client)

    @respx.mock
    def test_profile_falls_back_to_lean_fields(self, settings, api_client) -> None:
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "s", "user_id": 7})
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "l", "expires_in": 60})
        )
        respx.get("https://graph.instagram.com/v23.0/me").mock(
            side_effect=[
                httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "(#100) Tried accessing nonexisting field (followers_count)",
                            "code": 100,
                        }
                    },
                ),
                httpx.Response(200, json={"id": "7", "username": "lean", "account_type": "BUSINESS"}),
            ]
        )
        account = exchange_code("code", settings=settings, client=api_client)
        assert account.username == "lean"
        assert account.followers_count is None


class TestRefresh:
    @respx.mock
    def test_refresh_returns_a_new_token_and_expiry(self, settings, api_client) -> None:
        respx.get("https://graph.instagram.com/refresh_access_token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "refreshed", "expires_in": 5_183_944}
            )
        )
        token, expires_at = refresh_long_lived_token(
            "old", settings=settings, client=api_client
        )
        assert token == "refreshed"
        assert expires_at is not None

    def test_facebook_flow_has_nothing_to_refresh(self, api_client) -> None:
        settings = Settings(
            instagram_app_id="id", instagram_app_secret="secret", meta_login_flow="facebook_login"
        )
        assert refresh_long_lived_token("tok", settings=settings, client=api_client) == (
            "tok",
            None,
        )


class TestFacebookFlow:
    @respx.mock
    def test_reads_the_instagram_account_linked_to_a_page(self, api_client) -> None:
        settings = Settings(
            instagram_app_id="id",
            instagram_app_secret="secret",
            meta_login_flow="facebook_login",
            public_base_url="https://example.test",
        )
        respx.get("https://graph.facebook.com/v23.0/oauth/access_token").mock(
            side_effect=[
                httpx.Response(200, json={"access_token": "short-user-token"}),
                httpx.Response(200, json={"access_token": "long-user-token", "expires_in": 5184000}),
            ]
        )
        respx.get("https://graph.facebook.com/v23.0/me/accounts").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "page-without-ig", "name": "Sem Instagram"},
                        {
                            "id": "page-1",
                            "name": "Minha Página",
                            "access_token": "page-token",
                            "instagram_business_account": {"id": "17841400000000009", "username": "loja"},
                        },
                    ]
                },
            )
        )
        respx.get("https://graph.facebook.com/v23.0/17841400000000009").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "17841400000000009",
                    "username": "loja",
                    "account_type": "BUSINESS",
                    "followers_count": 4321,
                },
            )
        )

        account = exchange_code("code", settings=settings, client=api_client)

        assert account.ig_user_id == "17841400000000009"
        assert account.access_token == "page-token"
        assert account.facebook_page_id == "page-1"
        assert account.followers_count == 4321

    @respx.mock
    def test_no_linked_instagram_account_is_a_clear_error(self, api_client) -> None:
        settings = Settings(
            instagram_app_id="id",
            instagram_app_secret="secret",
            meta_login_flow="facebook_login",
            public_base_url="https://example.test",
        )
        respx.get("https://graph.facebook.com/v23.0/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok"})
        )
        respx.get("https://graph.facebook.com/v23.0/me/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "page-1"}]})
        )

        with pytest.raises(OAuthError, match="No Instagram Business account"):
            exchange_code("code", settings=settings, client=api_client)
