"""End-to-end tests for the collection pipeline against a mocked Graph API."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from app.models import Media, MetricSnapshot, RunStatus
from app.services.collector import collect_account, collect_all_accounts

GRAPH = "https://graph.instagram.com/v23.0"

PROFILE = {
    "id": "17841400000000001",
    "username": "test.account",
    "account_type": "BUSINESS",
    "followers_count": 10_000,
    "media_count": 2,
}

MEDIA_PAGE = {
    "data": [
        {
            "id": "media-1",
            "caption": "Um Reels sobre métricas",
            "media_type": "VIDEO",
            "media_product_type": "REELS",
            "permalink": "https://www.instagram.com/reel/abc/",
            "thumbnail_url": "https://cdn.example/thumb1.jpg",
            "timestamp": "2026-08-30T12:00:00+0000",
            "like_count": 400,
            "comments_count": 25,
        },
        {
            "id": "media-2",
            "caption": "Post de feed",
            "media_type": "IMAGE",
            "media_product_type": "FEED",
            "permalink": "https://www.instagram.com/p/def/",
            "timestamp": "2026-08-28T09:30:00+0000",
            "like_count": 150,
            "comments_count": 4,
        },
    ]
}


def _insights(**values: int) -> dict:
    return {"data": [{"name": name, "values": [{"value": value}]} for name, value in values.items()]}


def _mock_happy_path() -> None:
    respx.get(f"{GRAPH}/17841400000000001").mock(return_value=httpx.Response(200, json=PROFILE))
    respx.get(f"{GRAPH}/17841400000000001/media").mock(
        return_value=httpx.Response(200, json=MEDIA_PAGE)
    )
    respx.get(f"{GRAPH}/media-1/insights").mock(
        return_value=httpx.Response(
            200, json=_insights(reach=5000, saved=120, shares=60, views=9000, total_interactions=605)
        )
    )
    respx.get(f"{GRAPH}/media-2/insights").mock(
        return_value=httpx.Response(
            200, json=_insights(reach=2000, saved=40, shares=10, views=2000, total_interactions=204)
        )
    )


class TestHappyPath:
    @respx.mock
    def test_creates_media_and_snapshots(self, db, account, api_client) -> None:
        _mock_happy_path()

        result = collect_account(db, account, client=api_client)

        assert result.status is RunStatus.SUCCESS
        assert result.media_seen == 2
        assert result.media_created == 2
        assert result.snapshots_created == 2
        assert result.error is None

        media = db.scalars(select(Media).order_by(Media.ig_media_id)).all()
        assert [m.ig_media_id for m in media] == ["media-1", "media-2"]
        assert media[0].media_product_type == "REELS"
        assert media[0].timestamp is not None

        snapshot = db.scalar(
            select(MetricSnapshot).where(MetricSnapshot.media_id == media[0].id)
        )
        assert snapshot.likes == 400
        assert snapshot.comments == 25
        assert snapshot.saved == 120
        assert snapshot.reach == 5000
        assert snapshot.views == 9000
        # (400 + 25 + 120 + 60) / 5000 = 12.1%
        assert snapshot.engagement_rate == 12.1
        assert snapshot.engagement_basis == "reach"
        assert snapshot.raw["media"]["id"] == "media-1"

    @respx.mock
    def test_second_run_adds_history_without_duplicating_media(
        self, db, account, api_client
    ) -> None:
        _mock_happy_path()
        collect_account(db, account, client=api_client)
        second = collect_account(db, account, client=api_client)

        assert second.media_created == 0
        assert db.scalar(select(Media.id).where(Media.ig_media_id == "media-1")) is not None
        assert len(db.scalars(select(Media)).all()) == 2
        assert len(db.scalars(select(MetricSnapshot)).all()) == 4

    @respx.mock
    def test_profile_counters_are_refreshed(self, db, account, api_client) -> None:
        _mock_happy_path()
        account.followers_count = 1
        db.add(account)
        db.commit()

        collect_account(db, account, client=api_client)
        db.refresh(account)
        assert account.followers_count == 10_000
        assert account.last_collected_at is not None


class TestDegradedPaths:
    @respx.mock
    def test_unsupported_metrics_are_dropped_and_the_call_retried(
        self, db, account, api_client
    ) -> None:
        respx.get(f"{GRAPH}/17841400000000001").mock(return_value=httpx.Response(200, json=PROFILE))
        respx.get(f"{GRAPH}/17841400000000001/media").mock(
            return_value=httpx.Response(200, json={"data": [MEDIA_PAGE["data"][0]]})
        )
        route = respx.get(f"{GRAPH}/media-1/insights").mock(
            side_effect=[
                httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "(#100) The metric views is not supported for this media",
                            "code": 100,
                        }
                    },
                ),
                httpx.Response(200, json=_insights(reach=1000, saved=10, shares=5)),
            ]
        )

        result = collect_account(db, account, client=api_client)

        assert result.status is RunStatus.SUCCESS
        assert route.call_count == 2
        snapshot = db.scalar(select(MetricSnapshot))
        assert snapshot.reach == 1000
        assert snapshot.views is None  # dropped, not faked as zero

    @respx.mock
    def test_one_failing_insight_yields_a_partial_run(self, db, account, api_client) -> None:
        respx.get(f"{GRAPH}/17841400000000001").mock(return_value=httpx.Response(200, json=PROFILE))
        respx.get(f"{GRAPH}/17841400000000001/media").mock(
            return_value=httpx.Response(200, json=MEDIA_PAGE)
        )
        respx.get(f"{GRAPH}/media-1/insights").mock(
            return_value=httpx.Response(
                500, json={"error": {"message": "temporary failure", "code": 1}}
            )
        )
        respx.get(f"{GRAPH}/media-2/insights").mock(
            return_value=httpx.Response(200, json=_insights(reach=2000, saved=40, shares=10))
        )

        result = collect_account(db, account, client=api_client)

        assert result.status is RunStatus.PARTIAL
        assert result.snapshots_created == 2
        assert "media-1" in result.error
        # The post whose insights failed still has its like/comment counts.
        failed = db.scalar(
            select(MetricSnapshot)
            .join(Media, Media.id == MetricSnapshot.media_id)
            .where(Media.ig_media_id == "media-1")
        )
        assert failed.likes == 400
        assert failed.reach is None

    @respx.mock
    def test_an_expired_token_deactivates_the_account(self, db, account, api_client) -> None:
        respx.get(f"{GRAPH}/17841400000000001").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": {
                        "message": "Error validating access token: Session has expired",
                        "code": 190,
                        "error_subcode": 463,
                    }
                },
            )
        )

        result = collect_account(db, account, client=api_client)

        assert result.status is RunStatus.FAILED
        assert "reconnect" in (result.error or "").lower()
        db.refresh(account)
        assert account.is_active is False

    @respx.mock
    def test_an_account_with_no_media_still_succeeds(self, db, account, api_client) -> None:
        respx.get(f"{GRAPH}/17841400000000001").mock(return_value=httpx.Response(200, json=PROFILE))
        respx.get(f"{GRAPH}/17841400000000001/media").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        result = collect_account(db, account, client=api_client)
        assert result.status is RunStatus.SUCCESS
        assert result.media_seen == 0

    def test_an_undecryptable_token_fails_cleanly(self, db, account) -> None:
        account.access_token_encrypted = "not-a-valid-fernet-token"
        db.add(account)
        db.commit()

        result = collect_account(db, account)
        assert result.status is RunStatus.FAILED
        assert "decrypted" in result.error


class TestCollectAll:
    @respx.mock
    def test_skips_inactive_accounts(self, db, account) -> None:
        _mock_happy_path()
        account.is_active = False
        db.add(account)
        db.commit()

        assert collect_all_accounts(db) == []

    @respx.mock
    def test_runs_for_every_active_account(self, db, account) -> None:
        _mock_happy_path()
        results = collect_all_accounts(db)
        assert len(results) == 1
        assert results[0].ok


class TestAbortMidRun:
    @respx.mock
    def test_a_token_error_on_the_second_post_aborts_and_rolls_back(
        self, db, account, api_client
    ) -> None:
        respx.get(f"{GRAPH}/17841400000000001").mock(return_value=httpx.Response(200, json=PROFILE))
        respx.get(f"{GRAPH}/17841400000000001/media").mock(
            return_value=httpx.Response(200, json=MEDIA_PAGE)
        )
        respx.get(f"{GRAPH}/media-1/insights").mock(
            return_value=httpx.Response(200, json=_insights(reach=1000))
        )
        respx.get(f"{GRAPH}/media-2/insights").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": {
                        "message": "Error validating access token",
                        "code": 190,
                        "error_subcode": 463,
                    }
                },
            )
        )

        result = collect_account(db, account, client=api_client)

        assert result.status is RunStatus.FAILED
        assert result.snapshots_created == 0
        # Nothing from the aborted run is left half-written.
        assert db.scalars(select(MetricSnapshot)).all() == []
        db.refresh(account)
        assert account.is_active is False
