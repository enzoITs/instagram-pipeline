"""Where the two halves meet: official metrics for owned links, and link tracking."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.analyzer import graph_source, tracking
from app.analyzer.models import EngagementMetrics, Video, VideoMetricSnapshot, VideoStatus
from app.analyzer.pipeline import run_analysis
from app.analyzer.services.scraper_service import ScrapedPost
from app.database import session_scope
from app.instagram.url import parse_instagram_url
from app.models import Media

GRAPH = "https://graph.instagram.com/v23.0"
SHORTCODE = "C8xYz12AbCd"
URL = f"https://www.instagram.com/reel/{SHORTCODE}/"


@pytest.fixture(autouse=True)
def _clean_first(db_engine):
    """Both `db` and `db_engine` empty the tables; emptying first keeps the account."""
    return db_engine


@pytest.fixture()
def owned_media(db, account) -> Media:
    """A Reel of the connected account whose permalink matches `URL`."""
    media = Media(
        account_id=account.id,
        ig_media_id="media-9",
        media_type="VIDEO",
        media_product_type="REELS",
        caption="Legenda guardada",
        permalink=URL,
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


def _mock_graph(comments: list[dict] | None = None, comments_status: int = 200) -> None:
    respx.get(f"{GRAPH}/media-9").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "media-9",
                "caption": "Legenda oficial",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "timestamp": "2026-09-01T12:00:00+0000",
                "like_count": 300,
                "comments_count": 20,
            },
        )
    )
    respx.get(f"{GRAPH}/media-9/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"name": name, "values": [{"value": value}]}
                    for name, value in {"reach": 4000, "saved": 50, "shares": 30, "views": 8000}.items()
                ]
            },
        )
    )
    respx.get(f"{GRAPH}/media-9/comments").mock(
        return_value=httpx.Response(
            comments_status,
            json={"data": comments or []}
            if comments_status == 200
            else {"error": {"message": "missing permission", "code": 10}},
        )
    )


class TestOwnedPost:
    def test_a_link_of_nobody_here_is_not_owned(self, db, account) -> None:
        assert graph_source.load_owned_post(db, parse_instagram_url(URL)) is None

    def test_an_inactive_account_does_not_count(self, db, account, owned_media) -> None:
        account.is_active = False
        db.commit()
        assert graph_source.find_owned_media(db, SHORTCODE) is None

    @respx.mock
    def test_official_metrics_come_from_the_graph_api(self, db, owned_media, api_client) -> None:
        _mock_graph()
        post, media_id = graph_source.load_owned_post(db, parse_instagram_url(URL), client=api_client)

        assert media_id == owned_media.id
        assert post.source == "graph_api"
        assert (post.likes, post.comments_count) == (300, 20)
        assert (post.reach, post.saves, post.shares, post.views) == (4000, 50, 30, 8000)
        assert post.caption == "Legenda oficial"
        assert post.creator_username == "test.account"
        assert post.followers_count == 10_000
        assert post.comments == []  # ENABLE_GRAPH_COMMENTS is off

    @respx.mock
    def test_comments_come_from_the_api_when_enabled(self, db, owned_media, api_client, settings) -> None:
        _mock_graph(
            comments=[
                {"id": "c1", "text": "  Amei   isso ", "username": "fan", "like_count": 3},
                {"id": "c2", "text": "   "},
            ]
        )
        settings.enable_graph_comments = True
        try:
            post, _ = graph_source.load_owned_post(db, parse_instagram_url(URL), client=api_client)
        finally:
            settings.enable_graph_comments = False

        assert [(c.comment_id, c.text, c.author, c.likes) for c in post.comments] == [
            ("c1", "Amei isso", "fan", 3)
        ]

    @respx.mock
    def test_a_refused_comments_call_leaves_a_warning(self, db, owned_media, api_client, settings) -> None:
        _mock_graph(comments_status=400)
        settings.enable_graph_comments = True
        try:
            post, _ = graph_source.load_owned_post(db, parse_instagram_url(URL), client=api_client)
        finally:
            settings.enable_graph_comments = False

        assert post.comments == []
        assert post.warnings and "coleta pública" in post.warnings[0]

    @respx.mock
    def test_an_api_failure_falls_back_to_the_scraper(self, db, owned_media, api_client) -> None:
        respx.get(f"{GRAPH}/media-9").mock(
            return_value=httpx.Response(400, json={"error": {"message": "expired", "code": 190}})
        )
        assert graph_source.load_owned_post(db, parse_instagram_url(URL), client=api_client) is None

    def test_merging_keeps_official_numbers_and_scraped_comments(self) -> None:
        official = ScrapedPost(
            instagram_id=SHORTCODE, url=URL, media_type="reel", source="graph_api",
            likes=10, comments_count=None, reach=100, warnings=["a"],
        )
        scraped = ScrapedPost(
            instagram_id=SHORTCODE, url=URL, media_type="reel", source="apify",
            caption="scraped", likes=99, comments_count=5, views=500, warnings=["b"],
        )
        merged = graph_source.merge_official_metrics(official, scraped)

        assert merged.source == "graph_api+apify"
        assert (merged.likes, merged.comments_count, merged.views, merged.reach) == (10, 5, 500, 100)
        assert merged.caption == "scraped"
        assert merged.warnings == ["a", "b"]


class TestPipelineWithOfficialMetrics:
    @respx.mock
    def test_an_owned_link_is_analysed_with_reach(
        self, db, owned_media, api_client, make_video, pipeline_deps
    ) -> None:
        _mock_graph()
        video = make_video(instagram_id=SHORTCODE, url=URL)
        deps = pipeline_deps(
            graph_loader=lambda session, parsed: graph_source.load_owned_post(session, parsed, client=api_client)
        )

        assert run_analysis(video.id, deps=deps)["status"] == "completed"

        with session_scope() as session:
            stored = session.get(Video, video.id)
            assert stored.media_id == owned_media.id
            assert stored.data_source == "graph_api+mock"
            metrics = session.scalars(select(EngagementMetrics)).one()
            assert metrics.engagement_basis == "reach"
            assert (metrics.reach, metrics.saves, metrics.shares) == (4000, 50, 30)
            # (300 + 20 + 50 + 30) / 4000
            assert metrics.engagement_rate == 10.0

    def test_a_crashing_loader_does_not_sink_the_analysis(self, make_video, pipeline_deps) -> None:
        def broken(_session, _parsed):
            raise RuntimeError("boom")

        video = make_video()
        assert run_analysis(video.id, deps=pipeline_deps(graph_loader=broken))["status"] == "completed"
        with session_scope() as session:
            assert session.get(Video, video.id).data_source == "mock"


class TestTracking:
    async def test_tracking_is_switched_on_and_shown(self, client, make_video) -> None:
        video = make_video(status=VideoStatus.COMPLETED)
        response = await client.put(f"/api/v1/analysis/{video.id}/tracking", json={"tracked": True})
        assert response.status_code == 200
        assert response.json() == {"status": "success", "video_id": str(video.id), "tracked": True}

        with session_scope() as session:
            assert session.get(Video, video.id).tracked is True

    async def test_tracking_an_unknown_analysis_is_404(self, client) -> None:
        response = await client.put(
            "/api/v1/analysis/00000000-0000-0000-0000-000000000000/tracking", json={"tracked": True}
        )
        assert response.status_code == 404

    def test_only_completed_tracked_links_are_refreshed(self, make_video, pipeline_deps, sync_redis) -> None:
        tracked = make_video()
        run_analysis(tracked.id, deps=pipeline_deps())
        make_video(tracked=True)  # still processing: skipped
        make_video(status=VideoStatus.COMPLETED)  # not tracked: skipped
        with session_scope() as session:
            session.get(Video, tracked.id).tracked = True

        assert tracking.refresh_tracked_videos() == 1
        assert tracking.refresh_tracked_videos() == 1

        with session_scope() as session:
            snapshots = session.scalars(
                select(VideoMetricSnapshot).order_by(VideoMetricSnapshot.collected_at)
            ).all()
            # the analysis itself is the first point, each refresh adds one
            assert len(snapshots) == 3
            assert {row.video_id for row in snapshots} == {tracked.id}
            assert all(row.engagement_basis == "views" for row in snapshots)
            stored = session.get(Video, tracked.id)
            assert stored.last_tracked_at is not None
            metrics = session.scalars(select(EngagementMetrics)).one()
            assert metrics.views == snapshots[-1].views

            rows = tracking.snapshot_rows(list(reversed(snapshots)))
            assert [row["collected_at"] for row in rows] == [row.collected_at for row in snapshots]

    async def test_history_is_part_of_the_analysis(self, client, make_video, pipeline_deps) -> None:
        video = make_video()
        run_analysis(video.id, deps=pipeline_deps())
        # The finished analysis is cached; neither tracking nor a refresh may
        # leave that stale copy behind.
        assert (await client.get(f"/api/v1/analysis/{video.id}")).json()["data"]["tracked"] is False
        await client.put(f"/api/v1/analysis/{video.id}/tracking", json={"tracked": True})
        assert (await client.get(f"/api/v1/analysis/{video.id}")).json()["data"]["tracked"] is True

        tracking.refresh_tracked_videos()

        data = (await client.get(f"/api/v1/analysis/{video.id}")).json()["data"]
        assert data["tracked"] is True
        assert len(data["history"]) == 2
        assert data["history"][0]["engagement_basis"] == "views"

    def test_a_failing_link_does_not_stop_the_others(self, make_video, pipeline_deps, monkeypatch) -> None:
        first, second = make_video(), make_video()
        for video in (first, second):
            run_analysis(video.id, deps=pipeline_deps())
        with session_scope() as session:
            for video in (first, second):
                session.get(Video, video.id).tracked = True

        real = tracking.collect_current_metrics
        calls = []

        def flaky(db, video, **kwargs):
            calls.append(video.id)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real(db, video, **kwargs)

        monkeypatch.setattr(tracking, "collect_current_metrics", flaky)
        assert tracking.refresh_tracked_videos() == 1
        assert len(calls) == 2

    def test_an_unreachable_post_is_skipped(self, make_video, pipeline_deps, monkeypatch) -> None:
        from app.analyzer.services import scraper_service

        video = make_video()
        run_analysis(video.id, deps=pipeline_deps())
        with session_scope() as session:
            session.get(Video, video.id).tracked = True

        def refuse(self, _url):
            raise scraper_service.ScraperError("private", transient=False)

        monkeypatch.setattr(scraper_service.InstagramScraper, "scrape", refuse)
        assert tracking.refresh_tracked_videos() == 0

    @respx.mock
    def test_an_owned_link_is_tracked_through_the_api(
        self, db, owned_media, make_video, monkeypatch
    ) -> None:
        _mock_graph()
        video = make_video(instagram_id=SHORTCODE, url=URL, status=VideoStatus.COMPLETED, tracked=True)

        assert tracking.refresh_tracked_videos() == 1
        with session_scope() as session:
            snapshot = session.scalars(
                select(VideoMetricSnapshot).where(VideoMetricSnapshot.video_id == video.id)
            ).one()  # created directly as completed: no analysis snapshot
            assert snapshot.source == "graph_api"
            assert snapshot.engagement_basis == "reach"
            assert snapshot.reach == 4000
