"""Reddit trend source via the official OAuth API.

Uses the documented ``client_credentials`` application flow against oauth.reddit.com.
This is the sanctioned API path — nothing is scraped from reddit.com HTML.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
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

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_ROOT = "https://oauth.reddit.com"
VALID_LISTINGS = ("hot", "top", "rising", "new")

_token_lock = threading.Lock()
_cached_token: tuple[str, float] | None = None


@trend_registry.register
class RedditTrendProvider(TrendProvider):
    name = "reddit"

    @property
    def subreddit(self) -> str:
        value = str(self.config.get("subreddit", "")).strip().lstrip("r/").strip("/")
        if not value:
            raise ValidationError("This Reddit source has no subreddit configured.")
        if not value.replace("_", "").replace("+", "").isalnum():
            raise ValidationError(f"'{value}' is not a valid subreddit name.")
        return value

    @property
    def listing(self) -> str:
        listing = str(self.config.get("listing", "hot"))
        if listing not in VALID_LISTINGS:
            raise ValidationError(f"Unknown Reddit listing '{listing}'. Use one of {VALID_LISTINGS}.")
        return listing

    def availability(self) -> Availability:
        missing = []
        if not settings.reddit_client_id:
            missing.append("REDDIT_CLIENT_ID")
        if not settings.reddit_client_secret:
            missing.append("REDDIT_CLIENT_SECRET")
        if missing:
            return Availability.not_configured(
                provider=self.name,
                missing=tuple(missing),
                detail=(
                    "Create a 'script' application at https://www.reddit.com/prefs/apps and set "
                    "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT."
                ),
            )
        if not self.config.get("subreddit"):
            return Availability.not_configured(
                provider=self.name, missing=("subreddit",), detail="No subreddit configured."
            )
        return Availability(
            status=ComponentStatus.AVAILABLE,
            provider=self.name,
            detail=f"r/{self.subreddit} ({self.listing})",
            metadata={"subreddit": self.subreddit, "listing": self.listing},
        )

    @property
    def reliability(self) -> float:
        # Community signal: useful for demand, weaker as an evidentiary source.
        return float(self.config.get("reliability", 0.55))

    def fetch(self, *, limit: int = 50) -> TrendFetchResult:
        self.require()
        token = _access_token()
        capped = max(1, min(limit, 100))

        with http_client(
            headers={
                "authorization": f"Bearer {token}",
                "user-agent": settings.reddit_user_agent,
            }
        ) as client:
            payload = request_json(
                client,
                "GET",
                f"{API_ROOT}/r/{self.subreddit}/{self.listing}",
                provider="Reddit API",
                params={"limit": capped, "raw_json": 1},
            )

        children = (payload.get("data") or {}).get("children", [])
        items = [
            trend
            for trend in (self._normalize(child.get("data") or {}) for child in children)
            if trend is not None
        ]
        return TrendFetchResult(
            items=items,
            fetched_at=datetime.now(UTC),
            source_name=str(self.config.get("name") or f"r/{self.subreddit}"),
            provider=self.name,
        )

    def _normalize(self, post: dict[str, Any]) -> NormalizedTrend | None:
        post_id = post.get("id")
        title = (post.get("title") or "").strip()
        if not post_id or not title:
            return None
        if post.get("stickied") or post.get("over_18"):
            return None

        engagement: dict[str, Any] = {}
        for api_key, our_key in (
            ("score", "score"),
            ("num_comments", "comments"),
            ("upvote_ratio", "upvote_ratio"),
        ):
            if post.get(api_key) is not None:
                engagement[our_key] = post[api_key]

        created = post.get("created_utc")
        published_at = (
            datetime.fromtimestamp(float(created), tz=UTC) if isinstance(created, int | float) else None
        )

        return NormalizedTrend(
            external_id=str(post_id),
            title=title[:500],
            url=f"https://www.reddit.com{post.get('permalink', '')}" if post.get("permalink") else None,
            summary=(post.get("selftext") or "").strip()[:2000] or None,
            category=self.config.get("category"),
            language=self.config.get("language", "en"),
            author=post.get("author"),
            region=self.config.get("region"),
            published_at=published_at,
            engagement=engagement,
            raw={
                "subreddit": post.get("subreddit"),
                "external_link": post.get("url_overridden_by_dest"),
                "flair": post.get("link_flair_text"),
            },
        )


def _access_token() -> str:
    """Fetch (and briefly cache) an application-only token.

    Cached in process memory only: a credential like this never goes to the database.
    """
    global _cached_token
    with _token_lock:
        if _cached_token and _cached_token[1] > time.monotonic() + 60:
            return _cached_token[0]

        with http_client(headers={"user-agent": settings.reddit_user_agent}) as client:
            payload = request_json(
                client,
                "POST",
                TOKEN_URL,
                provider="Reddit OAuth",
                data={"grant_type": "client_credentials"},
                auth=(settings.reddit_client_id, settings.reddit_client_secret),
            )

        token = payload.get("access_token")
        if not token:
            from nexora.core.errors import ProviderUnavailable

            raise ProviderUnavailable("Reddit did not return an access token.")
        expires_in = float(payload.get("expires_in", 3600))
        _cached_token = (str(token), time.monotonic() + expires_in)
        return str(token)


def reset_token_cache() -> None:
    """Clear the cached token (used by tests and after a credential change)."""
    global _cached_token
    with _token_lock:
        _cached_token = None
