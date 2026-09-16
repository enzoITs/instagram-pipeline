from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.instagram_url import CANONICAL_URL_REGEX, InvalidInstagramURLError, parse_instagram_url
from app.schemas import AnalyzeRequest, InsightOut, MetricsOut, RecentItem, VideoOut, with_at


@pytest.mark.parametrize(
    ("raw", "canonical", "shortcode", "media_type"),
    [
        ("https://www.instagram.com/reel/ABC123/", "https://www.instagram.com/reel/ABC123/", "ABC123", "reel"),
        ("https://www.instagram.com/p/CxA1B2C3D4", "https://www.instagram.com/p/CxA1B2C3D4/", "CxA1B2C3D4", "post"),
        ("  https://instagram.com/reel/Ab_c-12/  ", "https://www.instagram.com/reel/Ab_c-12/", "Ab_c-12", "reel"),
        ("instagram.com/reels/XYZ987/", "https://www.instagram.com/reel/XYZ987/", "XYZ987", "reel"),
        ("http://m.instagram.com/p/QWERTY1/", "https://www.instagram.com/p/QWERTY1/", "QWERTY1", "post"),
        (
            "https://www.instagram.com/reel/C8xYz12AbCd/?igsh=MWQ1ZGUxMzBkMA==#top",
            "https://www.instagram.com/reel/C8xYz12AbCd/",
            "C8xYz12AbCd",
            "reel",
        ),
        ("https://www.instagram.com/some.brand/reel/DEF4567/", "https://www.instagram.com/reel/DEF4567/", "DEF4567", "reel"),
        ("https://www.instagram.com/p/GHI8901/c/1789/", "https://www.instagram.com/p/GHI8901/", "GHI8901", "post"),
    ],
)
def test_parse_instagram_url_accepts_common_variations(raw, canonical, shortcode, media_type):
    parsed = parse_instagram_url(raw)
    assert parsed.canonical_url == canonical
    assert parsed.shortcode == shortcode
    assert parsed.media_type == media_type
    assert CANONICAL_URL_REGEX.match(parsed.canonical_url)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a url",
        "https://www.youtube.com/watch?v=abc",
        "https://www.instagram.com/",
        "https://www.instagram.com/reel/",
        "https://www.instagram.com/stories/user/123/",
        "https://www.instagram.com/some.brand/",
        "https://www.instagram.com/reel/ab/",  # shortcode too short
        "https://www.instagram.com/reel/abc$%^/",
        "ftp://www.instagram.com/reel/ABC123/",
        "https://user:pass@www.instagram.com/reel/ABC123/",
        "https://www.instagram.com:8443/reel/ABC123/",
        "https://evil-instagram.com/reel/ABC123/",
        "https://www.instagram.com.evil.com/reel/ABC123/",
        "https://www.instagram.com/reel/ABC 123/",
        "https://www.instagram.com/reel/" + "A" * 3000,
    ],
)
def test_parse_instagram_url_rejects_invalid(raw):
    with pytest.raises(InvalidInstagramURLError):
        parse_instagram_url(raw)


def test_parse_instagram_url_rejects_non_string():
    with pytest.raises(InvalidInstagramURLError):
        parse_instagram_url(None)  # type: ignore[arg-type]


def test_analyze_request_normalizes_and_exposes_id():
    request = AnalyzeRequest(url="instagram.com/reel/C8xYz12AbCd/?igsh=abc")
    assert request.url == "https://www.instagram.com/reel/C8xYz12AbCd/"
    assert request.instagram_id == "C8xYz12AbCd"
    assert request.media_type == "reel"


def test_analyze_request_invalid_url_message():
    with pytest.raises(ValidationError) as exc_info:
        AnalyzeRequest(url="https://example.com/video")
    assert "URL inválida" in str(exc_info.value)


def test_with_at():
    assert with_at("brand") == "@brand"
    assert with_at("@brand") == "@brand"
    assert with_at(None) is None
    assert with_at("") is None


def test_datetimes_are_serialized_as_utc():
    naive = VideoOut(instagram_id="x", url="u", published_at=datetime(2024, 9, 15, 10, 30))
    assert naive.model_dump(mode="json")["published_at"] == "2024-09-15T10:30:00Z"
    aware = RecentItem.model_validate(
        {
            "id": "7b1e3c1e-1111-4f5e-9a1a-1234567890ab",
            "instagram_id": "x",
            "status": "completed",
            "created_at": datetime(2024, 9, 15, 10, 30, tzinfo=timezone.utc),
        }
    )
    assert aware.model_dump(mode="json")["created_at"] == "2024-09-15T10:30:00Z"


def test_metrics_and_insight_bounds():
    with pytest.raises(ValidationError):
        MetricsOut(engagement_score=11)
    with pytest.raises(ValidationError):
        InsightOut(type="strength", title="t", description="d", impact_score=-1)
    with pytest.raises(ValidationError):
        InsightOut(type="weakness", title="t", description="d", impact_score=5)


def test_settings_derives_sync_url_and_parses_lists():
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@db:5432/app",
        cors_origins=" http://a.com , http://b.com ,",
        apify_token="  ",
        openai_api_key="",
        instagram_username="",
        nlp_torch_threads="",  # compose passes unset variables as empty strings
    )
    assert settings.nlp_torch_threads is None
    assert Settings(nlp_torch_threads="4").nlp_torch_threads == 4
    assert settings.sync_database_url == "postgresql+psycopg2://u:p@db:5432/app"
    assert settings.cors_origin_list == ["http://a.com", "http://b.com"]
    assert settings.apify_token is None
    assert settings.openai_api_key is None
    assert settings.instagram_username is None
    assert not settings.is_sqlite
    sqlite = Settings(database_url="sqlite+aiosqlite:///tmp/x.db")
    assert sqlite.sync_database_url == "sqlite:///tmp/x.db"
    assert sqlite.is_sqlite
    assert Settings(database_url="mysql://x").sync_database_url == "mysql://x"
