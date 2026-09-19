"""Trend provider adapters.

Outbound HTTP is intercepted with respx. The interception exists ONLY in this test
module — production code has no mock path and no offline fallback.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from nexora.config import settings
from nexora.core.errors import (
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    UpstreamPermanentError,
    ValidationError,
)
from nexora.services.providers.trends import build_provider, trend_registry
from nexora.services.providers.trends.reddit import RedditTrendProvider, reset_token_cache
from nexora.services.providers.trends.rss import RssTrendProvider, validate_feed_url
from nexora.services.providers.trends.youtube import YouTubeTrendProvider
from tests.fixtures import feeds

FEED_URL = "https://fixture.invalid/feed.xml"


def test_fixtures_are_test_only() -> None:
    """Application code must never import the test fixtures.

    This is the guard that keeps a fixture from ever being served as real trend data.
    """
    api_root = Path(__file__).resolve().parents[1] / "nexora"
    result = subprocess.run(
        ["grep", "-rn", "tests.fixtures", str(api_root)], capture_output=True, text=True
    )
    assert result.stdout == "", f"Production code references test fixtures:\n{result.stdout}"


def test_registry_exposes_every_provider() -> None:
    assert trend_registry.names() == ["reddit", "rss", "youtube_data_api"]


# ------------------------------------------------------------------------------- RSS
def test_validate_feed_url_rejects_non_http_schemes() -> None:
    for bad in ("file:///etc/passwd", "ftp://host/feed", "javascript:alert(1)", "", "https://"):
        with pytest.raises(ValidationError):
            validate_feed_url(bad)
    assert validate_feed_url("https://example.com/rss") == "https://example.com/rss"


def test_rss_without_url_is_not_configured() -> None:
    availability = RssTrendProvider({}).availability()
    assert availability.status.value == "NOT CONFIGURED"
    assert availability.missing_settings == ("url",)
    with pytest.raises(ProviderNotConfigured):
        RssTrendProvider({}).fetch()


@respx.mock
def test_rss_2_0_parsing() -> None:
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(200, text=feeds.RSS_2_0, headers={"content-type": "application/rss+xml"})
    )
    result = RssTrendProvider({"url": FEED_URL, "category": "technology", "region": "GLOBAL"}).fetch()

    # The third fixture item has no title and must be skipped, not stored blank.
    assert len(result.items) == 2
    first = result.items[0]
    assert first.title == "TEST FIXTURE: semiconductor supply chain analysis"
    assert first.url == "https://fixture.invalid/articles/1"
    assert first.external_id == "fixture-item-1"
    assert first.published_at == datetime(2026, 9, 15, 8, 30, tzinfo=UTC)
    assert first.category == "technology"
    assert first.region == "GLOBAL"
    assert "<b>" not in (first.summary or ""), "HTML must be stripped from summaries"
    assert "Synthetic summary body" in (first.summary or "")
    # RSS exposes no engagement metrics — the map stays empty rather than zeroed.
    assert first.engagement == {}
    assert result.fetched_at.tzinfo is not None


@respx.mock
def test_atom_1_0_parsing() -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=feeds.ATOM_1_0))
    result = RssTrendProvider({"url": FEED_URL}).fetch()

    assert len(result.items) == 2
    first = result.items[0]
    assert first.title == "TEST FIXTURE: atom entry about energy storage"
    assert first.url == "https://fixture.invalid/atom/1"
    assert first.published_at == datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
    assert first.author == "Fixture Atom Author"
    # The second entry has no <published>; <updated> is used and must still be tz-aware.
    assert result.items[1].published_at == datetime(2026, 9, 16, 10, 30, tzinfo=UTC)


@respx.mock
def test_rss_empty_feed_returns_no_items_not_an_error() -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=feeds.EMPTY_FEED))
    result = RssTrendProvider({"url": FEED_URL}).fetch()
    assert result.items == []


@respx.mock
def test_rss_malformed_feed_reports_a_warning_and_invents_nothing() -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=feeds.MALFORMED_FEED))
    result = RssTrendProvider({"url": FEED_URL}).fetch()
    assert result.warnings, "a malformed feed must be reported, not silently accepted"
    assert all(item.title.strip() for item in result.items)


@respx.mock
def test_rss_http_error_raises_rather_than_returning_empty() -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(500, text="upstream exploded"))
    with pytest.raises(ProviderUnavailable):
        RssTrendProvider({"url": FEED_URL}).fetch()


@respx.mock
def test_rss_404_is_a_permanent_error() -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(404, text="gone"))
    with pytest.raises(UpstreamPermanentError):
        RssTrendProvider({"url": FEED_URL}).fetch()


@respx.mock
def test_rss_timeout_is_surfaced_as_unavailable() -> None:
    respx.get(FEED_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(ProviderUnavailable) as exc:
        RssTrendProvider({"url": FEED_URL}).fetch()
    assert "could not be fetched" in exc.value.message


# --------------------------------------------------------------------------- YouTube
def test_youtube_without_api_key_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "")
    availability = YouTubeTrendProvider({}).availability()
    assert availability.status.value == "NOT CONFIGURED"
    assert availability.missing_settings == ("YOUTUBE_API_KEY",)
    with pytest.raises(ProviderNotConfigured):
        YouTubeTrendProvider({}).fetch()


def test_youtube_rejects_an_invalid_region(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    with pytest.raises(ValidationError):
        YouTubeTrendProvider({"region_code": "USA"}).availability()


@respx.mock
def test_youtube_most_popular_normalization(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    route = respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_MOST_POPULAR)
    )
    result = YouTubeTrendProvider({"region_code": "GB", "category": "technology"}).fetch(limit=10)

    params = dict(route.calls[0].request.url.params)
    assert params["chart"] == "mostPopular"
    assert params["regionCode"] == "GB"
    assert params["maxResults"] == "10"
    assert params["key"] == "fixture-key"

    assert len(result.items) == 2, "the untitled fixture item must be skipped"
    first, second = result.items
    assert first.external_id == "FIXTUREVID1"
    assert first.url == "https://www.youtube.com/watch?v=FIXTUREVID1"
    assert first.engagement == {"views": 125000, "likes": 4100, "comments": 380}
    assert first.region == "GB"
    assert first.published_at == datetime(2026, 9, 17, 10, 0, tzinfo=UTC)

    # A hidden like count must be ABSENT, not zero.
    assert "likes" not in second.engagement
    assert second.engagement == {"views": 9400, "comments": 12}


@respx.mock
def test_youtube_caps_max_results_at_the_api_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    route = respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    YouTubeTrendProvider({}).fetch(limit=500)
    assert dict(route.calls[0].request.url.params)["maxResults"] == "50"


@respx.mock
def test_youtube_search_hydrates_statistics(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_SEARCH)
    )
    videos = respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(200, json=feeds.YOUTUBE_MOST_POPULAR)
    )
    result = YouTubeTrendProvider({"mode": "search", "query": "fixture"}).fetch()

    # Only the video result is hydrated; the channel result is discarded.
    assert dict(videos.calls[0].request.url.params)["id"] == "FIXTUREVID1"
    assert len(result.items) == 2


@respx.mock
def test_youtube_quota_exceeded_is_permanent_not_retried(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(403, json=feeds.YOUTUBE_QUOTA_ERROR)
    )
    with pytest.raises(UpstreamPermanentError) as exc:
        YouTubeTrendProvider({}).fetch()
    assert "exceeded your quota" in exc.value.message


@respx.mock
def test_youtube_rate_limit_is_typed_as_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(429, json={"error": {"message": "Too many requests"}},
                                    headers={"retry-after": "30"})
    )
    with pytest.raises(RateLimited) as exc:
        YouTubeTrendProvider({}).fetch()
    assert exc.value.details["retry_after"] == "30"


@respx.mock
def test_youtube_non_json_response_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    with pytest.raises(ProviderUnavailable):
        YouTubeTrendProvider({}).fetch()


@respx.mock
def test_youtube_timeout(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-key")
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(ProviderUnavailable) as exc:
        YouTubeTrendProvider({}).fetch()
    assert "timed out" in exc.value.message.lower()


# ---------------------------------------------------------------------------- Reddit
def test_reddit_without_credentials_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reddit_client_id", "")
    monkeypatch.setattr(settings, "reddit_client_secret", "")
    availability = RedditTrendProvider({"subreddit": "technology"}).availability()
    assert availability.status.value == "NOT CONFIGURED"
    assert set(availability.missing_settings) == {"REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"}


def test_reddit_rejects_an_invalid_subreddit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reddit_client_id", "id")
    monkeypatch.setattr(settings, "reddit_client_secret", "secret")
    with pytest.raises(ValidationError):
        assert RedditTrendProvider({"subreddit": "bad name!"}).subreddit
    with pytest.raises(ValidationError):
        assert RedditTrendProvider({"subreddit": "technology", "listing": "spicy"}).listing


@respx.mock
def test_reddit_fetch_normalizes_and_skips_stickied(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reddit_client_id", "id")
    monkeypatch.setattr(settings, "reddit_client_secret", "secret")
    reset_token_cache()

    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json=feeds.REDDIT_TOKEN)
    )
    listing = respx.get("https://oauth.reddit.com/r/fixture/hot").mock(
        return_value=httpx.Response(200, json=feeds.REDDIT_LISTING)
    )
    result = RedditTrendProvider({"subreddit": "fixture"}).fetch(limit=25)

    assert listing.calls[0].request.headers["authorization"] == "Bearer fixture-token"
    assert len(result.items) == 1, "stickied posts must be skipped"
    item = result.items[0]
    assert item.engagement == {"score": 2450, "comments": 318, "upvote_ratio": 0.94}
    assert item.url == "https://www.reddit.com/r/fixture/comments/fixturepost1/"
    assert item.published_at is not None and item.published_at.tzinfo is not None
    reset_token_cache()


@respx.mock
def test_reddit_auth_failure_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reddit_client_id", "id")
    monkeypatch.setattr(settings, "reddit_client_secret", "wrong")
    reset_token_cache()
    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    with pytest.raises(UpstreamPermanentError):
        RedditTrendProvider({"subreddit": "fixture"}).fetch()
    reset_token_cache()


@respx.mock
def test_reddit_token_is_cached_between_fetches(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reddit_client_id", "id")
    monkeypatch.setattr(settings, "reddit_client_secret", "secret")
    reset_token_cache()
    token = respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json=feeds.REDDIT_TOKEN)
    )
    respx.get("https://oauth.reddit.com/r/fixture/hot").mock(
        return_value=httpx.Response(200, json=feeds.REDDIT_LISTING)
    )
    provider = RedditTrendProvider({"subreddit": "fixture"})
    provider.fetch()
    provider.fetch()
    assert token.call_count == 1, "the token must not be re-requested for every fetch"
    reset_token_cache()


def test_build_provider_rejects_an_unknown_kind() -> None:
    with pytest.raises(ProviderNotConfigured):
        build_provider("carrier_pigeon", {})
