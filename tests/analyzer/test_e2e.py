"""End-to-end workflow through the HTTP API with the real pipeline (inline task backend,
mock scraper, lexicon NLP, rule-based insights) and external services mocked."""

import time

from sqlalchemy import func, select

from app.analyzer.models import Comment
from app.database import session_scope


async def test_full_analysis_workflow(client):
    url = "https://www.instagram.com/reel/E2eFlow123/"

    # 1. Submit: 202 immediately (the inline backend then runs the pipeline in background).
    submitted = await client.post("/api/v1/analyze", json={"url": url})
    assert submitted.status_code == 202
    video_id = submitted.json()["video_id"]

    # 2. Poll like the frontend does (max 60 attempts, 2 s apart in production).
    started = time.perf_counter()
    for _ in range(60):
        body = (await client.get(f"/api/v1/analysis/{video_id}")).json()
        if body["status"] != "processing":
            break
    assert body["status"] == "success", body
    assert time.perf_counter() - started < 120

    data = body["data"]
    assert data["video_id"] == video_id
    assert data["video"]["url"] == url
    analyzed = data["processing"]["comments_analyzed"]
    assert 100 <= analyzed <= 500
    assert sum(data["sentiment"].values()) == analyzed
    assert all(data["sentiment"][label] > 0 for label in ("positive", "negative", "neutral", "doubt", "spam"))
    assert all(data["intents"][label] > 0 for label in ("potential_buyer", "hater", "praise", "question", "other"))
    assert 3 <= len(data["insights"]) <= 5
    assert {insight["type"] for insight in data["insights"]} <= {"strength", "improvement", "trend"}
    assert data["metrics"]["engagement_rate"] is not None
    assert data["top_topics"]

    # 3. Same URL again: served from cache.
    again = await client.post("/api/v1/analyze", json={"url": url})
    assert again.status_code == 200 and again.json()["cached"] is True

    # 4. Listed as the most recent analysis.
    recent = (await client.get("/api/v1/recent")).json()
    assert recent["data"][0]["id"] == video_id
    assert recent["data"][0]["status"] == "completed"
    assert recent["data"][0]["engagement_score"] == data["metrics"]["engagement_score"]

    # 5. Delete: gone from API, cache and database.
    assert (await client.delete(f"/api/v1/analysis/{video_id}")).status_code == 200
    assert (await client.get(f"/api/v1/analysis/{video_id}")).status_code == 404
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Comment)) == 0
    resubmitted = await client.post("/api/v1/analyze", json={"url": url})
    assert resubmitted.status_code == 202
    assert resubmitted.json()["video_id"] != video_id


async def test_failed_scrape_is_reported_to_the_client(client, monkeypatch):
    from app.analyzer.services.scraper_service import InstagramScraper, ScraperError

    def private_post(self, url):
        raise ScraperError("post privado ou exige login", transient=False)

    monkeypatch.setattr(InstagramScraper, "scrape", private_post)
    submitted = await client.post("/api/v1/analyze", json={"url": "https://www.instagram.com/p/Private99/"})
    body = (await client.get(f"/api/v1/analysis/{submitted.json()['video_id']}")).json()
    assert body["status"] == "error"
    assert "privado" in body["message"]
