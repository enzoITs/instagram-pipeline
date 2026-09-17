import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import instaloader
import pytest
import respx
from instaloader import exceptions as il_errors

from app.analyzer.services import scraper_service
from app.analyzer.services.mock_data import MOCK_WARNING
from app.analyzer.services.scraper_service import (
    InstagramScraper,
    ScraperError,
    parse_timestamp,
    retry_call,
    to_count,
)
from app.config import Settings
from app.instagram.url import InvalidInstagramURLError

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.instagram.com/reel/C8xYz12AbCd/"
POST_ENDPOINT = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
COMMENTS_ENDPOINT = "https://api.apify.com/v2/acts/apify~instagram-comment-scraper/run-sync-get-dataset-items"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def make_settings(**overrides) -> Settings:
    base = {"scraper_mode": "apify", "apify_token": "apify_api_TESTTOKEN123456", "max_comments": 500}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(scraper_service.time, "sleep", lambda _s: None)


# --------------------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    ("value", "expected"),
    [(10, 10), ("42", 42), (3.9, 3), (-1, None), (None, None), (True, None), ("abc", None), ("7.0", 7)],
)
def test_to_count(value, expected):
    assert to_count(value) == expected


def test_parse_timestamp_variants():
    expected = datetime(2024, 9, 15, 10, 30, tzinfo=timezone.utc)
    assert parse_timestamp("2024-09-15T10:30:00.000Z") == expected
    assert parse_timestamp("2024-09-15T10:30:00") == expected
    assert parse_timestamp(int(expected.timestamp())) == expected
    assert parse_timestamp(int(expected.timestamp()) * 1000) == expected
    assert parse_timestamp(datetime(2024, 9, 15, 10, 30)) == expected
    assert parse_timestamp(expected) == expected
    assert parse_timestamp("not a date") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(10**20) is None
    assert parse_timestamp(["x"]) is None


def test_retry_call_retries_transient_then_succeeds():
    calls = []
    sleeps = []

    def flaky(remaining: float) -> str:
        calls.append(remaining)
        if len(calls) < 3:
            raise ScraperError("temporário", transient=True)
        return "ok"

    result = retry_call(flaky, attempts=3, deadline=time.monotonic() + 60, operation="op", sleep=sleeps.append)
    assert result == "ok"
    assert len(calls) == 3
    assert 1 <= sleeps[0] < 1.6 and 2 <= sleeps[1] < 2.6  # exponential backoff with jitter


def test_retry_call_does_not_retry_permanent_errors():
    calls = []

    def permanent(_remaining: float) -> None:
        calls.append(1)
        raise ScraperError("não encontrado", transient=False)

    with pytest.raises(ScraperError, match="não encontrado"):
        retry_call(permanent, attempts=3, deadline=time.monotonic() + 60, operation="op")
    assert len(calls) == 1


def test_retry_call_respects_deadline():
    with pytest.raises(ScraperError, match="orçamento"):
        retry_call(lambda _r: "never", attempts=3, deadline=time.monotonic() - 1, operation="op")

    def always_transient(_remaining: float) -> None:
        raise ScraperError("lento", transient=True)

    with pytest.raises(ScraperError, match="lento"):  # next backoff would cross the deadline
        retry_call(always_transient, attempts=3, deadline=time.monotonic() + 1.5, operation="op")


def test_static_url_helpers():
    assert InstagramScraper.validate_url("instagram.com/p/ABC1234") == "https://www.instagram.com/p/ABC1234/"
    assert InstagramScraper.extract_instagram_id(URL) == "C8xYz12AbCd"
    with pytest.raises(InvalidInstagramURLError):
        InstagramScraper.validate_url("https://example.com")


# --------------------------------------------------------------------------- Apify
@respx.mock
def test_apify_success_normalizes_post_and_comments():
    post_route = respx.post(POST_ENDPOINT).mock(return_value=httpx.Response(201, json=load("apify_post.json")))
    comments_route = respx.post(COMMENTS_ENDPOINT).mock(
        return_value=httpx.Response(201, json=load("apify_comments.json"))
    )

    with InstagramScraper(make_settings()) as scraper:
        post = scraper.scrape(URL)

    request = post_route.calls.last.request
    assert request.headers["Authorization"] == "Bearer apify_api_TESTTOKEN123456"
    assert "TESTTOKEN" not in str(request.url)  # token never in URLs (they end up in logs)
    assert json.loads(request.content)["directUrls"] == [URL]
    assert json.loads(comments_route.calls.last.request.content)["resultsLimit"] == 500

    assert post.source == "apify"
    assert post.instagram_id == "C8xYz12AbCd"
    assert post.creator_username == "brand"
    assert post.views == 250000  # play count preferred over view count for Reels
    assert post.likes == 18500
    assert post.comments_count == 1230
    assert post.shares is None and post.saves is None
    assert post.duration_sec == 30.5
    assert post.published_at == datetime(2024, 9, 15, 10, 30, tzinfo=timezone.utc)
    assert post.thumbnail_url.endswith("thumb.jpg")

    texts = [comment.text for comment in post.comments]
    assert texts == ["Adorei! Quando vende?", "Produto incrível", "Sem id, recebe hash"]
    assert post.comments[1].author == "nested_owner"
    assert post.comments[1].likes == 7
    assert post.comments[2].comment_id.startswith("h_")
    assert post.comments[2].likes == 0
    assert post.warnings == []


@respx.mock
def test_apify_comments_failure_falls_back_to_latest_comments():
    respx.post(POST_ENDPOINT).mock(return_value=httpx.Response(201, json=load("apify_post.json")))
    respx.post(COMMENTS_ENDPOINT).mock(return_value=httpx.Response(402, json={"error": "payment"}))

    post = InstagramScraper(make_settings()).scrape(URL)
    assert [comment.text for comment in post.comments] == ["Comentário da amostra"]
    assert any("comentários falhou" in warning for warning in post.warnings)


@respx.mock
def test_apify_transient_errors_are_retried():
    respx.post(POST_ENDPOINT).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(201, json=load("apify_post.json")),
        ]
    )
    respx.post(COMMENTS_ENDPOINT).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(201, json=load("apify_comments.json"))]
    )
    post = InstagramScraper(make_settings()).scrape(URL)
    assert post.likes == 18500
    assert len(post.comments) == 3


@respx.mock
def test_apify_gives_up_after_max_attempts():
    route = respx.post(POST_ENDPOINT).mock(return_value=httpx.Response(429))
    respx.post(COMMENTS_ENDPOINT).mock(return_value=httpx.Response(429))
    with pytest.raises(ScraperError) as exc_info:
        InstagramScraper(make_settings()).scrape(URL)
    assert exc_info.value.transient is True
    assert route.call_count == 3


@respx.mock
def test_apify_invalid_payloads():
    respx.post(COMMENTS_ENDPOINT).mock(return_value=httpx.Response(201, json=[]))
    route = respx.post(POST_ENDPOINT)

    route.mock(return_value=httpx.Response(201, json={"not": "a list"}))
    with pytest.raises(ScraperError, match="formato"):
        InstagramScraper(make_settings()).scrape(URL)

    route.mock(return_value=httpx.Response(201, json=[{"error": "not_found", "errorDescription": "Post is private"}]))
    with pytest.raises(ScraperError, match="Post is private") as exc_info:
        InstagramScraper(make_settings()).scrape(URL)
    assert exc_info.value.transient is False

    route.mock(return_value=httpx.Response(201, json=[]))
    with pytest.raises(ScraperError, match="sem resultados"):
        InstagramScraper(make_settings()).scrape(URL)

    route.mock(return_value=httpx.Response(201, content=b"<html>"))
    with pytest.raises(ScraperError, match="JSON"):
        InstagramScraper(make_settings(apify_max_attempts=1)).scrape(URL)


def test_apify_requires_token():
    with pytest.raises(ScraperError, match="APIFY_TOKEN"):
        InstagramScraper(Settings(scraper_mode="apify", apify_token=None)).scrape(URL)


# --------------------------------------------------------------------------- Instaloader
class FakeComment(SimpleNamespace):
    pass


def fake_post(comments=None, comments_error=None, **overrides):
    def get_comments():
        if comments_error:
            raise comments_error
        yield from comments or []

    fields = {
        "likes": 900,
        "comments": 12,
        "caption": "Legenda",
        "owner_username": "creator",
        "url": "https://cdn/thumb.jpg",
        "date_utc": datetime(2024, 1, 2, 3, 4, 5),
        "is_video": True,
        "video_play_count": None,
        "video_view_count": 15000,
        "video_duration": 12.0,
        "get_comments": get_comments,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def instaloader_scraper(monkeypatch, post_or_error, **settings_overrides):
    def from_shortcode(_context, shortcode):
        assert shortcode == "C8xYz12AbCd"
        if isinstance(post_or_error, Exception):
            raise post_or_error
        return post_or_error

    monkeypatch.setattr(instaloader.Post, "from_shortcode", staticmethod(from_shortcode))
    loader = SimpleNamespace(context=object(), sessions=[])
    loader.load_session_from_file = lambda user, path: loader.sessions.append((user, path))
    settings = Settings(scraper_mode="instaloader", **settings_overrides)
    return InstagramScraper(settings, instaloader_factory=lambda: loader), loader


def test_instaloader_success(monkeypatch):
    comments = [
        FakeComment(id=1, text="Muito bom", owner=SimpleNamespace(username="ana"), likes_count=5,
                    created_at_utc=datetime(2024, 1, 2, 5, 0)),
        FakeComment(id=2, text="Quanto custa?", owner=SimpleNamespace(username="bia"), likes_count=0,
                    created_at_utc=datetime(2024, 1, 2, 6, 0)),
    ]
    scraper, loader = instaloader_scraper(
        monkeypatch, fake_post(comments), instagram_username="me", instagram_sessionfile="/tmp/session"
    )
    post = scraper.scrape(URL)
    assert loader.sessions == [("me", "/tmp/session")]
    assert post.source == "instaloader"
    assert post.views == 15000
    assert post.likes == 900
    assert post.published_at.tzinfo is not None
    assert [comment.author for comment in post.comments] == ["ana", "bia"]
    assert post.warnings == []


def test_instaloader_comments_require_login(monkeypatch):
    scraper, _ = instaloader_scraper(
        monkeypatch, fake_post(comments_error=il_errors.LoginRequiredException("login"), is_video=False)
    )
    post = scraper.scrape(URL)
    assert post.comments == []
    assert post.views is None and post.duration_sec is None
    assert any("INSTAGRAM_SESSIONFILE" in warning for warning in post.warnings)


def test_instaloader_comments_interrupted(monkeypatch):
    scraper, _ = instaloader_scraper(monkeypatch, fake_post(comments_error=il_errors.ConnectionException("x")))
    assert any("interrompida" in warning for warning in scraper.scrape(URL).warnings)


@pytest.mark.parametrize(
    ("error", "transient", "message"),
    [
        (il_errors.QueryReturnedNotFoundException("404"), False, "não encontrado"),
        (il_errors.LoginRequiredException("login"), False, "privado"),
        (il_errors.TooManyRequestsException("429"), True, "recusou"),
        (il_errors.BadResponseException("bad"), True, "falha"),
    ],
)
def test_instaloader_metadata_errors(monkeypatch, error, transient, message):
    scraper, _ = instaloader_scraper(monkeypatch, error)
    with pytest.raises(ScraperError, match=message) as exc_info:
        scraper.scrape(URL)
    assert exc_info.value.transient is transient


def test_instaloader_session_load_failure_is_a_warning(monkeypatch):
    scraper, loader = instaloader_scraper(
        monkeypatch, fake_post(), instagram_username="me", instagram_sessionfile="/missing"
    )

    def broken(_user, _path):
        raise FileNotFoundError("missing")

    loader.load_session_from_file = broken
    assert any("Sessão" in warning for warning in scraper.scrape(URL).warnings)


def test_real_instaloader_is_built_with_fail_fast_rate_controller():
    scraper = InstagramScraper(Settings(scraper_mode="instaloader"))
    loader = scraper._build_instaloader(instaloader)
    controller = loader.context._rate_controller
    with pytest.raises(il_errors.TooManyRequestsException):
        controller.sleep(60)
    controller.sleep(0)  # short waits are allowed
    loader.close()


# --------------------------------------------------------------------------- strategy chain
@respx.mock
def test_auto_mode_falls_back_to_instaloader_when_apify_fails(monkeypatch):
    respx.post(POST_ENDPOINT).mock(return_value=httpx.Response(401))
    respx.post(COMMENTS_ENDPOINT).mock(return_value=httpx.Response(401))
    scraper, _ = instaloader_scraper(monkeypatch, fake_post())
    scraper.settings = make_settings(scraper_mode="auto")
    post = scraper.scrape(URL)
    assert post.source == "instaloader"
    assert any("Fonte primária indisponível" in warning for warning in post.warnings)


def test_auto_mode_without_token_uses_only_instaloader(monkeypatch):
    scraper, _ = instaloader_scraper(monkeypatch, il_errors.TooManyRequestsException("429"))
    scraper.settings = Settings(scraper_mode="auto", apify_token=None)
    assert [name for name, _ in scraper._strategies()] == ["instaloader"]
    with pytest.raises(ScraperError) as exc_info:
        scraper.scrape(URL)
    assert exc_info.value.transient is True
    assert "instaloader" in str(exc_info.value)


def test_mock_mode_is_deterministic_and_flagged():
    scraper = InstagramScraper(Settings(scraper_mode="mock", max_comments=500))
    first = scraper.scrape(URL)
    second = scraper.scrape("instagram.com/reel/C8xYz12AbCd")
    assert first.source == "mock"
    assert MOCK_WARNING in first.warnings
    assert 180 <= len(first.comments) <= 320
    assert [c.text for c in first.comments] == [c.text for c in second.comments]
    assert len({c.comment_id for c in first.comments}) == len(first.comments)
    capped = InstagramScraper(Settings(scraper_mode="mock", max_comments=50)).scrape(URL)
    assert len(capped.comments) == 50
    assert InstagramScraper(Settings(scraper_mode="mock")).scrape("instagram.com/p/C8xYz12AbCd").duration_sec is None
