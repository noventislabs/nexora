"""Live verification against the real YouTube Data API.

Excluded from the default run (``-m 'not live'``) and skipped entirely when no API key
is configured. Run with ``pytest -m live``.

These assertions are deliberately about *shape and invariants*, never about specific
video titles or view counts — those change every hour, and a test that asserted them
would be asserting a fiction.
"""

from __future__ import annotations

import pytest

from nexora.config import settings
from nexora.core.errors import UpstreamPermanentError, ValidationError
from nexora.services.providers.trends.youtube import CATEGORY_IDS, YouTubeTrendProvider
from tests.live import live_credential

pytestmark = pytest.mark.live

API_KEY = live_credential("YOUTUBE_API_KEY")
requires_key = pytest.mark.skipif(not API_KEY, reason="YOUTUBE_API_KEY is not configured")


@pytest.fixture
def youtube(monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", API_KEY or "")
    return YouTubeTrendProvider


@requires_key
def test_most_popular_chart_returns_normalized_items(youtube) -> None:
    result = youtube({"region_code": "US", "category": "technology"}).fetch(limit=5)

    assert 1 <= len(result.items) <= 5
    assert result.fetched_at.tzinfo is not None

    for item in result.items:
        assert item.external_id
        assert item.title.strip()
        assert item.url == f"https://www.youtube.com/watch?v={item.external_id}"
        assert item.region == "US"
        assert item.published_at is not None and item.published_at.tzinfo is not None
        assert item.author
        # Views are always public on the chart; likes/comments may be hidden, and when
        # they are, the key must be ABSENT rather than zero.
        assert isinstance(item.engagement.get("views"), int)
        for optional in ("likes", "comments"):
            if optional in item.engagement:
                assert isinstance(item.engagement[optional], int)


@requires_key
def test_region_is_honoured(youtube) -> None:
    us = youtube({"region_code": "US", "category": "technology"}).fetch(limit=5)
    gb = youtube({"region_code": "GB", "category": "technology"}).fetch(limit=5)

    assert {item.region for item in us.items} == {"US"}
    assert {item.region for item in gb.items} == {"GB"}


@requires_key
def test_category_filter_actually_reaches_the_api(youtube) -> None:
    """Guards the bug where an unmapped category silently produced a general chart."""
    params = youtube({"region_code": "US", "category": "technology"})._params(5)
    assert params["videoCategoryId"] == CATEGORY_IDS["technology"]

    filtered = youtube({"region_code": "US", "category": "technology"}).fetch(limit=10)
    unfiltered = youtube({"region_code": "US"}).fetch(limit=10)
    assert filtered.items and unfiltered.items
    # Different charts: a category filter that did nothing would return the same ids.
    assert {item.external_id for item in filtered.items} != {
        item.external_id for item in unfiltered.items
    }


@requires_key
def test_unmapped_category_is_rejected_rather_than_ignored(youtube) -> None:
    with pytest.raises(ValidationError):
        youtube({"region_code": "US", "category": "not-a-real-category"}).fetch(limit=1)


@requires_key
def test_search_mode_returns_hydrated_statistics(youtube) -> None:
    result = youtube(
        {"mode": "search", "query": "semiconductor manufacturing", "window_days": 30}
    ).fetch(limit=5)

    assert result.items, "the search returned nothing; the query may need widening"
    for item in result.items:
        assert item.external_id and item.title.strip()
        # search.list carries no statistics; these must have been hydrated by videos.list.
        assert "views" in item.engagement


@requires_key
def test_an_invalid_key_is_a_permanent_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "AIzaSyINVALID-KEY-FOR-TESTING-0000000000")
    with pytest.raises(UpstreamPermanentError):
        YouTubeTrendProvider({"region_code": "US"}).fetch(limit=1)


@requires_key
def test_an_invalid_region_is_a_permanent_error(youtube) -> None:
    with pytest.raises(UpstreamPermanentError):
        youtube({"region_code": "ZZ"}).fetch(limit=1)
