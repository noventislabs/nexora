"""RSS/Atom trend source.

Reads publicly published feeds. This is a permitted, intended use of a feed — it is
not scraping, and no page is fetched outside the feed the operator configured.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from nexora.core.errors import ProviderUnavailable, ValidationError
from nexora.db.models.enums import ComponentStatus
from nexora.services.http import http_client, raise_for_upstream
from nexora.services.providers.base import Availability
from nexora.services.providers.trends.base import (
    NormalizedTrend,
    TrendFetchResult,
    TrendProvider,
    trend_registry,
)

MAX_FEED_BYTES = 5 * 1024 * 1024


def validate_feed_url(url: str) -> str:
    """Only http(s) URLs with a host. Blocks file://, gopher:// and friends."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Feed URL must use http or https.")
    if not parsed.netloc:
        raise ValidationError("Feed URL must include a host.")
    return parsed.geturl()


@trend_registry.register
class RssTrendProvider(TrendProvider):
    name = "rss"

    @property
    def url(self) -> str:
        return str(self.config.get("url", ""))

    def availability(self) -> Availability:
        if not self.url:
            return Availability.not_configured(
                provider=self.name,
                missing=("url",),
                detail="This RSS source has no feed URL configured.",
            )
        try:
            validate_feed_url(self.url)
        except ValidationError as exc:
            return Availability.unavailable(provider=self.name, detail=str(exc))
        # RSS needs no credentials, so configuration alone makes it usable.
        return Availability(
            status=ComponentStatus.AVAILABLE,
            provider=self.name,
            detail=f"RSS feed {self.url}",
            metadata={"url": self.url},
        )

    def fetch(self, *, limit: int = 50) -> TrendFetchResult:
        self.require()
        url = validate_feed_url(self.url)
        try:
            with http_client(
                headers={"accept": "application/rss+xml, application/xml, text/xml, */*"}
            ) as client:
                response = client.get(url)
                raise_for_upstream(response, provider=f"RSS feed {url}")
                payload = response.content[:MAX_FEED_BYTES]
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"RSS feed could not be fetched: {exc}") from exc

        parsed = feedparser.parse(payload)
        warnings: list[str] = []
        if parsed.get("bozo") and parsed.get("bozo_exception"):
            warnings.append(f"Feed is not well-formed: {parsed['bozo_exception']}")

        items: list[NormalizedTrend] = []
        for entry in parsed.entries[:limit]:
            normalized = self._normalize(entry)
            if normalized is not None:
                items.append(normalized)

        return TrendFetchResult(
            items=items,
            fetched_at=datetime.now(UTC),
            source_name=str(self.config.get("name") or parsed.feed.get("title") or url),
            provider=self.name,
            warnings=warnings,
        )

    def _normalize(self, entry: Any) -> NormalizedTrend | None:
        title = (entry.get("title") or "").strip()
        if not title:
            return None
        link = (entry.get("link") or "").strip() or None
        external_id = (entry.get("id") or link or title)[:255]

        published: datetime | None = None
        for key in ("published_parsed", "updated_parsed"):
            struct = entry.get(key)
            if struct:
                published = datetime.fromtimestamp(time.mktime(struct), tz=UTC)
                break

        summary = (entry.get("summary") or "").strip() or None
        if summary:
            summary = _strip_html(summary)[:2000]

        return NormalizedTrend(
            external_id=external_id,
            title=title[:500],
            url=link,
            summary=summary,
            category=self.config.get("category"),
            language=self.config.get("language") or entry.get("language"),
            author=(entry.get("author") or "").strip() or None,
            # A feed does not state a geography; only an operator-configured value is used.
            region=self.config.get("region"),
            published_at=published,
            # A feed reports no engagement metrics; the map stays empty rather than
            # inventing zeros.
            engagement={},
            raw={"tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")]},
        )


def _strip_html(value: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()
