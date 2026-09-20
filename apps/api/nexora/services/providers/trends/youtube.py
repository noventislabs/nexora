"""YouTube Data API v3 trend source.

Uses the official API with a server API key. Two modes:

* ``mostPopular`` — the videos.list chart for a region and optional category.
* ``search`` — search.list for a query, ordered by view count over a recent window.

Only fields the API actually returns are kept. When a channel hides its statistics,
``statistics`` keys are absent from the response and stay absent here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexora.config import settings
from nexora.core.errors import ValidationError
from nexora.db.models.enums import ComponentStatus
from nexora.services.http import http_client, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.trends.base import (
    NormalizedTrend,
    TrendFetchResult,
    TrendProvider,
    trend_registry,
)

API_ROOT = "https://www.googleapis.com/youtube/v3"

#: Maps NEXORA's content-category vocabulary onto YouTube's own video category ids, so
#: a scan configured for a channel category actually filters the chart it requests.
#: Keys cover the seeded vocabulary in ``nexora.services.categories.SEED_CATEGORIES``;
#: an unmapped value raises rather than silently producing an unfiltered chart, which
#: is what made a technology scan quietly return music videos.
#:
#: A channel-invented category has no YouTube equivalent by definition. Such a scan
#: must name a YouTube category explicitly instead of expecting one to be guessed.
CATEGORY_IDS = {
    # Film & Animation
    "anime": "1",
    "animation": "1",
    "kids": "1",
    # Entertainment
    "entertainment": "24",
    "family": "24",
    "documentary": "24",
    # Music
    "music": "10",
    # Gaming
    "gaming": "20",
    # Sports
    "sports": "17",
    # Travel & Events
    "travel": "19",
    # Howto & Style
    "lifestyle": "26",
    "health": "26",
    "food": "26",
    "diy": "26",
    # Education
    "education": "27",
    "history": "27",
    # Science & Technology
    "technology": "28",
    "ai": "28",
    "science": "28",
    "future": "28",
    # News & Politics — where business, finance and policy coverage lives on YouTube.
    "business": "25",
    "finance": "25",
    "digital_economy": "25",
    "global_developments": "25",
    "news": "25",
    "commentary": "25",
    # Direct YouTube category names, for operators who prefer them.
    "film_animation": "1",
    "science_technology": "28",
    "news_politics": "25",
    "howto_style": "26",
    "people_blogs": "22",
    "comedy": "23",
}

VALID_MODES = ("mostPopular", "search")


@trend_registry.register
class YouTubeTrendProvider(TrendProvider):
    name = "youtube_data_api"

    @property
    def mode(self) -> str:
        mode = str(self.config.get("mode", "mostPopular"))
        if mode not in VALID_MODES:
            raise ValidationError(f"Unknown YouTube trend mode '{mode}'. Use one of {VALID_MODES}.")
        return mode

    @property
    def region_code(self) -> str:
        region = str(self.config.get("region_code", "US")).upper()
        if len(region) != 2 or not region.isalpha():
            raise ValidationError("region_code must be a two-letter ISO 3166-1 country code.")
        return region

    def availability(self) -> Availability:
        if not settings.youtube_api_key:
            return Availability.not_configured(
                provider=self.name,
                missing=("YOUTUBE_API_KEY",),
                detail=(
                    "A YouTube Data API v3 key is required to read public trend data. "
                    "Create one in Google Cloud Console with the YouTube Data API enabled."
                ),
            )
        if self.mode == "search" and not self.config.get("query"):
            return Availability.not_configured(
                provider=self.name,
                missing=("query",),
                detail="This source is in search mode but has no query configured.",
            )
        if self.mode == "mostPopular":
            self._params(1)  # raises ValidationError on an unmappable category
        return Availability(
            status=ComponentStatus.AVAILABLE,
            provider=self.name,
            detail=f"YouTube Data API ({self.mode}, region {self.region_code})",
            metadata={"mode": self.mode, "region_code": self.region_code},
        )

    @property
    def reliability(self) -> float:
        # First-party platform data about the platform we publish to.
        return float(self.config.get("reliability", 0.95))

    def fetch(self, *, limit: int = 50) -> TrendFetchResult:
        self.require()
        params = self._params(limit)
        endpoint = f"{API_ROOT}/videos" if self.mode == "mostPopular" else f"{API_ROOT}/search"

        with http_client() as client:
            payload = request_json(client, "GET", endpoint, provider="YouTube Data API", params=params)
            items = payload.get("items", [])
            if self.mode == "search":
                # search.list returns no statistics; fetch them for the ids it returned.
                items = self._hydrate_search_results(client, items)

        return TrendFetchResult(
            items=[trend for trend in (self._normalize(item) for item in items) if trend],
            fetched_at=datetime.now(UTC),
            source_name=str(self.config.get("name") or f"YouTube {self.mode} {self.region_code}"),
            provider=self.name,
        )

    def _params(self, limit: int) -> dict[str, Any]:
        capped = max(1, min(limit, 50))  # API maximum is 50 per page.
        if self.mode == "mostPopular":
            params: dict[str, Any] = {
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": self.region_code,
                "maxResults": capped,
                "key": settings.youtube_api_key,
            }
            category_id = self.config.get("category_id")
            if not category_id:
                configured = str(self.config.get("category", "")).strip().lower()
                if configured:
                    category_id = CATEGORY_IDS.get(configured)
                    if category_id is None:
                        # Silently dropping the filter would return an unrelated chart
                        # while appearing to have worked.
                        raise ValidationError(
                            f"No YouTube video category maps to '{configured}'. "
                            f"Known values: {', '.join(sorted(CATEGORY_IDS))}. "
                            "Set 'category_id' explicitly to use a category id directly."
                        )
            if category_id:
                params["videoCategoryId"] = str(category_id)
            return params

        published_after = datetime.now(UTC) - timedelta(days=int(self.config.get("window_days", 7)))
        return {
            "part": "snippet",
            "q": str(self.config["query"]),
            "type": "video",
            "order": str(self.config.get("order", "viewCount")),
            "regionCode": self.region_code,
            "relevanceLanguage": str(self.config.get("language", "en")),
            "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": capped,
            "key": settings.youtube_api_key,
        }

    def _hydrate_search_results(self, client: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        video_ids = [
            item["id"]["videoId"]
            for item in items
            if isinstance(item.get("id"), dict) and item["id"].get("videoId")
        ]
        if not video_ids:
            return []
        payload = request_json(
            client,
            "GET",
            f"{API_ROOT}/videos",
            provider="YouTube Data API",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids[:50]),
                "key": settings.youtube_api_key,
            },
        )
        return payload.get("items", [])

    def _normalize(self, item: dict[str, Any]) -> NormalizedTrend | None:
        video_id = item.get("id")
        if isinstance(video_id, dict):
            video_id = video_id.get("videoId")
        snippet = item.get("snippet") or {}
        title = (snippet.get("title") or "").strip()
        if not video_id or not title:
            return None

        # Only keys the API actually returned. A hidden like count is absent, not zero.
        statistics = item.get("statistics") or {}
        engagement: dict[str, Any] = {}
        for api_key, our_key in (
            ("viewCount", "views"),
            ("likeCount", "likes"),
            ("commentCount", "comments"),
        ):
            if api_key in statistics:
                try:
                    engagement[our_key] = int(statistics[api_key])
                except (TypeError, ValueError):
                    continue

        published_at = _parse_iso(snippet.get("publishedAt"))
        return NormalizedTrend(
            external_id=str(video_id),
            title=title[:500],
            url=f"https://www.youtube.com/watch?v={video_id}",
            summary=(snippet.get("description") or "").strip()[:2000] or None,
            category=self.config.get("category") or snippet.get("categoryId"),
            language=snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
            author=snippet.get("channelTitle"),
            # The chart/search was requested for this region, so the scope is real.
            region=self.region_code,
            published_at=published_at,
            engagement=engagement,
            raw={
                "channel_id": snippet.get("channelId"),
                "tags": snippet.get("tags", [])[:25],
                "duration": (item.get("contentDetails") or {}).get("duration"),
            },
        )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
