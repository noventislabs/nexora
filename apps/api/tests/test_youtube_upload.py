"""Resumable upload mechanics and metadata limits."""

from __future__ import annotations

import io
import json

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from nexora.config import settings
from nexora.core.errors import (
    ProviderUnavailable,
    RateLimited,
    UpstreamPermanentError,
    ValidationError,
)
from nexora.services.metadata import (
    MAX_TAGS_TOTAL_CHARS,
    MAX_TITLE_CHARS,
    build_source_section,
    validate_description,
    validate_tags,
    validate_title,
)
from nexora.services.providers.youtube.google import (
    UPLOAD_CHUNK_BYTES,
    GoogleYouTubeProvider,
    _parse_duration,
    critical_tag_limit,
)
from tests.fixtures import feeds

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SESSION_URL = "https://upload.googleapis.com/fixture-session"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "fixture-client-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "fixture-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


def upload_args(size: int = 1024, **overrides):
    return {
        "media": io.BytesIO(b"\x00" * size),
        "media_size": size,
        "title": "Fixture title",
        "description": "Fixture description.",
        "tags": ["fixture"],
        "privacy_status": "private",
        **overrides,
    }


@respx.mock
def test_a_small_upload_completes_in_one_chunk(oauth_configured) -> None:
    start = respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"location": SESSION_URL})
    )
    put = respx.put(SESSION_URL).mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO)
    )

    result = GoogleYouTubeProvider().upload_video("token", **upload_args(1024))

    assert result.video_id == "FIXTUREUPLOAD1"
    assert result.privacy_status == "private"
    assert put.call_count == 1

    body = json.loads(start.calls[0].request.content)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert start.calls[0].request.headers["x-upload-content-length"] == "1024"
    assert put.calls[0].request.headers["content-range"] == "bytes 0-1023/1024"


@respx.mock
def test_a_large_upload_is_streamed_in_chunks(oauth_configured) -> None:
    """A 1080p render must never be buffered whole; it goes up in bounded chunks."""
    size = UPLOAD_CHUNK_BYTES * 2 + 1000
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))

    responses = [
        httpx.Response(308, headers={"range": f"bytes=0-{UPLOAD_CHUNK_BYTES - 1}"}),
        httpx.Response(308, headers={"range": f"bytes=0-{UPLOAD_CHUNK_BYTES * 2 - 1}"}),
        httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO),
    ]
    put = respx.put(SESSION_URL).mock(side_effect=responses)

    result = GoogleYouTubeProvider().upload_video("token", **upload_args(size))

    assert result.video_id == "FIXTUREUPLOAD1"
    assert put.call_count == 3
    ranges = [call.request.headers["content-range"] for call in put.calls]
    assert ranges[0] == f"bytes 0-{UPLOAD_CHUNK_BYTES - 1}/{size}"
    assert ranges[-1] == f"bytes {UPLOAD_CHUNK_BYTES * 2}-{size - 1}/{size}"


@respx.mock
def test_the_upload_resumes_from_the_offset_google_reports(oauth_configured) -> None:
    """Google is the authority on how much it holds, not our own byte counter."""
    size = UPLOAD_CHUNK_BYTES * 2
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))
    # Google reports fewer bytes received than we sent; we must rewind and resend.
    responses = [
        httpx.Response(308, headers={"range": "bytes=0-999"}),
        httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO),
    ]
    put = respx.put(SESSION_URL).mock(side_effect=responses)

    GoogleYouTubeProvider().upload_video("token", **upload_args(size))

    assert put.calls[1].request.headers["content-range"].startswith("bytes 1000-")


@respx.mock
def test_a_scheduled_publish_requires_private(oauth_configured) -> None:
    from datetime import UTC, datetime, timedelta

    with pytest.raises(ValidationError) as exc:
        GoogleYouTubeProvider().upload_video(
            "token",
            **upload_args(privacy_status="public", publish_at=datetime.now(UTC) + timedelta(days=1)),
        )
    assert "privacy_status='private'" in exc.value.message


@respx.mock
def test_a_scheduled_publish_sends_publish_at(oauth_configured) -> None:
    from datetime import UTC, datetime, timedelta

    start = respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"location": SESSION_URL})
    )
    respx.put(SESSION_URL).mock(return_value=httpx.Response(200, json=feeds.YOUTUBE_UPLOADED_VIDEO))

    when = datetime.now(UTC) + timedelta(days=1)
    GoogleYouTubeProvider().upload_video("token", **upload_args(publish_at=when))

    body = json.loads(start.calls[0].request.content)
    assert body["status"]["publishAt"].endswith("Z")


def test_an_invalid_privacy_status_is_refused(oauth_configured) -> None:
    with pytest.raises(ValidationError):
        GoogleYouTubeProvider().upload_video("token", **upload_args(privacy_status="semi-public"))


def test_an_empty_render_is_refused(oauth_configured) -> None:
    with pytest.raises(ValidationError):
        GoogleYouTubeProvider().upload_video("token", **upload_args(0))


@respx.mock
def test_quota_exhaustion_is_permanent(oauth_configured) -> None:
    respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(403, json=feeds.YOUTUBE_QUOTA_ERROR)
    )
    with pytest.raises(UpstreamPermanentError):
        GoogleYouTubeProvider().upload_video("token", **upload_args())


@respx.mock
def test_rate_limiting_is_typed_separately(oauth_configured) -> None:
    respx.post(UPLOAD_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )
    with pytest.raises(RateLimited):
        GoogleYouTubeProvider().upload_video("token", **upload_args())


@respx.mock
def test_a_dropped_connection_mid_upload_is_reported(oauth_configured) -> None:
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"location": SESSION_URL}))
    respx.put(SESSION_URL).mock(side_effect=httpx.ConnectError("connection reset"))

    with pytest.raises(ProviderUnavailable) as exc:
        GoogleYouTubeProvider().upload_video("token", **upload_args())
    assert "connection failed" in exc.value.message


@respx.mock
def test_a_missing_session_url_is_an_error(oauth_configured) -> None:
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(200))
    with pytest.raises(ProviderUnavailable) as exc:
        GoogleYouTubeProvider().upload_video("token", **upload_args())
    assert "session URL" in exc.value.message


@respx.mock
def test_an_oversized_thumbnail_is_refused_before_upload(oauth_configured) -> None:
    route = respx.post(THUMBNAIL_URL).mock(return_value=httpx.Response(200))
    with pytest.raises(ValidationError) as exc:
        GoogleYouTubeProvider().set_thumbnail(
            "token", video_id="x", image=b"\x00" * (3 * 1024 * 1024), mime_type="image/png"
        )
    assert "2 MB" in exc.value.message
    assert route.call_count == 0


@respx.mock
def test_an_empty_uploads_playlist_returns_no_videos(oauth_configured) -> None:
    """YouTube 404s an empty uploads playlist; that means zero videos, not an error."""
    respx.get("https://www.googleapis.com/youtube/v3/channels").mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_MY_CHANNEL)
    )
    respx.get("https://www.googleapis.com/youtube/v3/playlistItems").mock(
        return_value=httpx.Response(404, json={"error": {"message": "playlist not found"}})
    )
    assert GoogleYouTubeProvider().list_channel_videos("token") == []


# -------------------------------------------------------------------- metadata
def test_youtube_limits_are_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_title("x" * (MAX_TITLE_CHARS + 1))
    with pytest.raises(ValidationError):
        validate_title("A <script> title")
    with pytest.raises(ValidationError):
        validate_description("x" * 5001)


def test_tags_are_trimmed_to_the_total_character_limit() -> None:
    tags, warnings = validate_tags(["semiconductor manufacturing"] * 60)
    total = sum(len(tag) + 1 for tag in tags)
    assert total <= MAX_TAGS_TOTAL_CHARS
    assert any("trimmed" in warning for warning in warnings)


def test_an_overlong_individual_tag_is_dropped() -> None:
    tags, warnings = validate_tags(["fine", "x" * 40])
    assert tags == ["fine"]
    assert any("exceeds 30 characters" in warning for warning in warnings)


def test_the_provider_also_caps_tags_at_the_api_limit() -> None:
    assert critical_tag_limit(["a" * 100] * 10) < 10
    assert critical_tag_limit(["short"]) == 1


def test_source_attribution_is_built_from_real_documents() -> None:
    class Document:
        def __init__(self, title, publisher, url):
            self.title, self.publisher, self.url = title, publisher, url

    section = build_source_section(
        [
            Document("Fabrication capacity report", "Fixture Press", "https://fixture.invalid/1"),
            Document("", None, None),  # unusable, must be skipped
        ]
    )
    assert "Sources used in this video:" in section
    assert "Fabrication capacity report — Fixture Press" in section
    assert "https://fixture.invalid/1" in section
    assert section.count("•") == 1


def test_no_sources_produces_no_attribution_section() -> None:
    assert build_source_section([]) == ""


def test_iso_duration_parsing() -> None:
    assert _parse_duration("PT8M12S") == 492
    assert _parse_duration("PT1H2M3S") == 3723
    assert _parse_duration("PT45S") == 45
    assert _parse_duration("nonsense") is None
    assert _parse_duration(None) is None
