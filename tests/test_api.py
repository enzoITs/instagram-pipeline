"""Tests for the HTTP API and the static dashboard entry point."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.models import CollectionRun, Media, MetricSnapshot, RunStatus

GRAPH = "https://graph.instagram.com/v23.0"


@pytest.fixture()
def populated(db, account):
    """One account with two posts and two collection runs of history."""
    older = datetime.now(timezone.utc) - timedelta(hours=6)
    newer = datetime.now(timezone.utc)

    reel = Media(
        account_id=account.id,
        ig_media_id="media-reel",
        media_type="VIDEO",
        media_product_type="REELS",
        caption="Reels sobre métricas",
        permalink="https://www.instagram.com/reel/abc/",
        timestamp=newer - timedelta(days=1),
    )
    post = Media(
        account_id=account.id,
        ig_media_id="media-feed",
        media_type="IMAGE",
        media_product_type="FEED",
        caption="Post de feed",
        timestamp=newer - timedelta(days=5),
    )
    db.add_all([reel, post])
    db.flush()

    rows = [
        (reel, older, 100, 10, 20, 5, 1000, 3000, 13.5),
        (reel, newer, 200, 20, 40, 10, 2000, 6000, 13.5),
        (post, older, 50, 2, 5, 1, 500, 500, 11.6),
        (post, newer, 60, 3, 6, 2, 600, 600, 11.83),
    ]
    for media, when, likes, comments, saved, shares, reach, views, rate in rows:
        db.add(
            MetricSnapshot(
                media_id=media.id, collected_at=when, likes=likes, comments=comments,
                saved=saved, shares=shares, reach=reach, views=views,
                total_interactions=likes + comments + saved + shares,
                engagement_rate=rate, engagement_basis="reach",
            )
        )
    reel.last_snapshot_at = newer
    reel.latest_engagement_rate = 13.5
    post.last_snapshot_at = newer
    post.latest_engagement_rate = 11.83

    db.add(
        CollectionRun(
            account_id=account.id, started_at=newer, finished_at=newer,
            status=RunStatus.SUCCESS, trigger="scheduled",
            media_seen=2, media_created=0, snapshots_created=2, api_calls=4,
        )
    )
    db.commit()
    return {"account": account, "reel": reel, "post": post}


class TestSystem:
    def test_health_reports_configuration(self, http_client) -> None:
        body = http_client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["meta_configured"] is True
        assert body["login_flow"] == "instagram_login"
        assert body["scheduler_running"] is False

    def test_dashboard_and_docs_are_served(self, http_client) -> None:
        page = http_client.get("/")
        assert page.status_code == 200
        assert "Instagram Pipeline" in page.text
        assert http_client.get("/static/app.js").status_code == 200
        assert http_client.get("/docs").status_code == 200


class TestAccounts:
    def test_empty_account_list(self, http_client) -> None:
        assert http_client.get("/api/accounts").json() == []

    def test_lists_and_filters_accounts(self, http_client, account, db) -> None:
        assert len(http_client.get("/api/accounts").json()) == 1
        account.is_active = False
        db.add(account)
        db.commit()
        assert http_client.get("/api/accounts?include_inactive=false").json() == []

    def test_summary_aggregates_only_the_newest_snapshot(self, http_client, populated) -> None:
        account_id = populated["account"].id
        body = http_client.get(f"/api/accounts/{account_id}/summary").json()

        assert body["tracked_media"] == 2
        assert body["snapshots"] == 4
        # Newest snapshots only: 200 + 60 likes, not all four rows.
        assert body["total_likes"] == 260
        assert body["total_reach"] == 2600
        assert body["average_engagement_rate"] == pytest.approx(12.67, abs=0.01)
        assert body["account"]["username"] == "test.account"

    def test_summary_of_an_account_without_data(self, http_client, account) -> None:
        body = http_client.get(f"/api/accounts/{account.id}/summary").json()
        assert body["tracked_media"] == 0
        assert body["total_likes"] == 0
        assert body["average_engagement_rate"] is None

    def test_timeseries_has_one_point_per_collection(self, http_client, populated) -> None:
        account_id = populated["account"].id
        points = http_client.get(f"/api/accounts/{account_id}/timeseries?days=30").json()

        assert len(points) == 2
        assert points[0]["likes"] == 150  # 100 + 50 at the older collection
        assert points[1]["likes"] == 260
        assert points[0]["media_count"] == 2
        # Timestamps carry an explicit UTC marker so the browser does not
        # reinterpret them as local time.
        assert points[0]["collected_at"].endswith("Z")

    def test_timeseries_respects_the_range(self, http_client, populated) -> None:
        account_id = populated["account"].id
        assert http_client.get(f"/api/accounts/{account_id}/timeseries?days=1").json()
        assert http_client.get(f"/api/accounts/{account_id}/timeseries?days=800").status_code == 422

    def test_breakdown_groups_by_product_type(self, http_client, populated) -> None:
        account_id = populated["account"].id
        rows = http_client.get(f"/api/accounts/{account_id}/breakdown").json()
        by_type = {row["media_product_type"]: row for row in rows}

        assert set(by_type) == {"REELS", "FEED"}
        assert by_type["REELS"]["media_count"] == 1
        assert by_type["REELS"]["total_reach"] == 2000

    def test_unknown_account_returns_404(self, http_client) -> None:
        assert http_client.get("/api/accounts/9999/summary").status_code == 404


class TestMedia:
    def test_lists_media_with_the_newest_snapshot_attached(self, http_client, populated) -> None:
        account_id = populated["account"].id
        body = http_client.get(f"/api/accounts/{account_id}/media").json()

        assert body["total"] == 2
        first = body["items"][0]
        assert first["ig_media_id"] == "media-reel"  # newest first by default
        assert first["latest"]["likes"] == 200

    def test_sorting_by_engagement_rate(self, http_client, populated) -> None:
        account_id = populated["account"].id
        body = http_client.get(
            f"/api/accounts/{account_id}/media?order_by=engagement_rate&direction=asc"
        ).json()
        assert [item["ig_media_id"] for item in body["items"]] == ["media-feed", "media-reel"]

    def test_filtering_by_product_type_and_caption(self, http_client, populated) -> None:
        account_id = populated["account"].id
        by_type = http_client.get(
            f"/api/accounts/{account_id}/media?media_product_type=REELS"
        ).json()
        assert by_type["total"] == 1

        by_search = http_client.get(f"/api/accounts/{account_id}/media?search=feed").json()
        assert by_search["total"] == 1
        assert by_search["items"][0]["ig_media_id"] == "media-feed"

    def test_pagination(self, http_client, populated) -> None:
        account_id = populated["account"].id
        page = http_client.get(f"/api/accounts/{account_id}/media?limit=1&offset=1").json()
        assert page["total"] == 2
        assert len(page["items"]) == 1
        assert page["offset"] == 1

    def test_invalid_ordering_is_rejected(self, http_client, populated) -> None:
        account_id = populated["account"].id
        response = http_client.get(f"/api/accounts/{account_id}/media?order_by=caption")
        assert response.status_code == 422

    def test_media_detail_returns_the_full_history(self, http_client, populated) -> None:
        media_id = populated["reel"].id
        body = http_client.get(f"/api/media/{media_id}").json()

        assert body["ig_media_id"] == "media-reel"
        assert len(body["snapshots"]) == 2
        # Oldest first, so the chart reads left to right.
        assert body["snapshots"][0]["likes"] == 100
        assert body["snapshots"][1]["likes"] == 200

    def test_unknown_media_returns_404(self, http_client) -> None:
        assert http_client.get("/api/media/9999").status_code == 404


class TestExport:
    def test_csv_contains_every_snapshot(self, http_client, populated) -> None:
        account_id = populated["account"].id
        response = http_client.get(f"/api/accounts/{account_id}/export.csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]

        lines = response.text.strip().splitlines()
        assert lines[0].startswith("collected_at,ig_media_id")
        assert len(lines) == 5  # header + 4 snapshots

    def test_latest_only_returns_one_row_per_post(self, http_client, populated) -> None:
        account_id = populated["account"].id
        response = http_client.get(f"/api/accounts/{account_id}/export.csv?latest_only=true")
        assert len(response.text.strip().splitlines()) == 3  # header + 2 posts


class TestJobs:
    def test_runs_are_listed_newest_first(self, http_client, populated) -> None:
        runs = http_client.get("/api/runs").json()
        assert len(runs) == 1
        assert runs[0]["status"] == "success"
        assert runs[0]["trigger"] == "scheduled"

    def test_collect_rejects_a_disconnected_account(self, http_client, account, db) -> None:
        account.is_active = False
        db.add(account)
        db.commit()
        response = http_client.post(f"/api/accounts/{account.id}/collect")
        assert response.status_code == 409

    @respx.mock
    def test_manual_collection_writes_snapshots(self, http_client, account) -> None:
        respx.route(host="testserver").pass_through()
        respx.get(f"{GRAPH}/{account.ig_user_id}").mock(
            return_value=httpx.Response(
                200, json={"id": account.ig_user_id, "username": "test.account", "followers_count": 10}
            )
        )
        respx.get(f"{GRAPH}/{account.ig_user_id}/media").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "media_type": "IMAGE",
                            "media_product_type": "FEED",
                            "timestamp": "2026-09-01T10:00:00+0000",
                            "like_count": 10,
                            "comments_count": 1,
                        }
                    ]
                },
            )
        )
        respx.get(f"{GRAPH}/m1/insights").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"name": "reach", "values": [{"value": 100}]},
                        {"name": "saved", "values": [{"value": 2}]},
                    ]
                },
            )
        )

        body = http_client.post(f"/api/accounts/{account.id}/collect").json()

        assert body["status"] == "success"
        assert body["snapshots_created"] == 1

        media = http_client.get(f"/api/accounts/{account.id}/media").json()
        assert media["items"][0]["latest"]["reach"] == 100


class TestAuth:
    def test_login_redirects_to_meta(self, http_client) -> None:
        response = http_client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].startswith("https://www.instagram.com/oauth/authorize")

    def test_login_url_returns_json(self, http_client) -> None:
        url = http_client.get("/auth/login-url").json()["url"]
        assert "client_id=test-app-id" in url

    def test_callback_without_a_code_redirects_with_an_error(self, http_client) -> None:
        response = http_client.get("/auth/callback", follow_redirects=False)
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_callback_rejects_a_forged_state(self, http_client) -> None:
        response = http_client.get(
            "/auth/callback?code=abc&state=forged", follow_redirects=False
        )
        assert response.status_code == 303
        assert "Invalid%20OAuth%20state" in response.headers["location"]

    def test_callback_surfaces_a_user_denial(self, http_client) -> None:
        response = http_client.get(
            "/auth/callback?error=access_denied&error_description=User+denied",
            follow_redirects=False,
        )
        assert "error=User%20denied" in response.headers["location"]

    def test_disconnect_clears_the_stored_token(self, http_client, account, db) -> None:
        body = http_client.delete(f"/auth/accounts/{account.id}").json()
        assert body["is_active"] is False
        db.expire_all()
        db.refresh(account)
        assert account.access_token_encrypted == ""


class TestAuthCallbackSuccess:
    @respx.mock
    def test_a_successful_callback_stores_the_account(self, http_client, db) -> None:
        from app.config import get_settings
        from app.crypto import decrypt_token
        from app.instagram.oauth import make_state
        from app.models import Account

        respx.route(host="testserver").pass_through()
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(
                200, json={"data": [{"access_token": "short", "user_id": 17841400000000123}]}
            )
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "long", "expires_in": 5183944})
        )
        respx.get(f"{GRAPH}/me").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "17841400000000123",
                    "username": "nova.conta",
                    "account_type": "BUSINESS",
                    "followers_count": 777,
                },
            )
        )

        state = make_state(get_settings())
        response = http_client.get(
            f"/auth/callback?code=abc%23_&state={state}", follow_redirects=False
        )

        assert response.status_code == 303
        assert "connected=" in response.headers["location"]

        stored = db.scalar(
            __import__("sqlalchemy").select(Account).where(
                Account.ig_user_id == "17841400000000123"
            )
        )
        assert stored is not None
        assert stored.username == "nova.conta"
        assert stored.is_active is True
        assert decrypt_token(stored.access_token_encrypted) == "long"
        # The plaintext token is never written to the column.
        assert "long" not in stored.access_token_encrypted

    @respx.mock
    def test_reconnecting_updates_the_existing_row(self, http_client, db, account) -> None:
        from app.config import get_settings
        from app.crypto import decrypt_token
        from app.instagram.oauth import make_state
        from app.models import Account

        account.is_active = False
        db.add(account)
        db.commit()

        respx.route(host="testserver").pass_through()
        respx.post("https://api.instagram.com/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "short", "user_id": 1})
        )
        respx.get("https://graph.instagram.com/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 5183944})
        )
        respx.get(f"{GRAPH}/me").mock(
            return_value=httpx.Response(
                200, json={"id": account.ig_user_id, "username": "test.account"}
            )
        )

        http_client.get(
            f"/auth/callback?code=abc&state={make_state(get_settings())}",
            follow_redirects=False,
        )

        db.expire_all()
        rows = db.scalars(__import__("sqlalchemy").select(Account)).all()
        assert len(rows) == 1  # updated in place, not duplicated
        assert rows[0].is_active is True
        assert decrypt_token(rows[0].access_token_encrypted) == "fresh"
