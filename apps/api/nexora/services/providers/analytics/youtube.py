"""YouTube Analytics API provider.

Separate from the Data API provider because it is a separate permission: reading a
channel's analytics needs the owner's consent through OAuth, and revenue needs a
further scope on top of that. Neither is ever inferred from public data.

The API returns a column-header/row table. This adapter maps it back to named metrics
and — critically — reports the metrics it asked for and did **not** get as
``unavailable`` rather than filling them with zero. A channel with 0 impressions and a
channel whose impressions we are not permitted to read are different facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable
from nexora.core.logging import get_logger
from nexora.services.http import http_client, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.youtube.base import (
    AnalyticsProvider,
    AnalyticsResult,
    analytics_registry,
)

logger = get_logger(__name__)

API_ROOT = "https://youtubeanalytics.googleapis.com/v2/reports"
SOURCE = "youtube_analytics_api"

#: Metrics every analytics-scoped connection can read.
CORE_METRICS = (
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "dislikes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
)

#: Requested separately: the API rejects the whole request if a metric is not valid for
#: the report, and losing the core metrics to get impressions would be a bad trade.
IMPRESSION_METRICS = ("impressions", "impressionClickThroughRate")

MONETARY_METRICS = ("estimatedRevenue", "estimatedAdRevenue", "cpm", "playbackBasedCpm")

#: API name → the name NEXORA stores. Storing the API's own casing would leak an
#: upstream detail into every consumer of a snapshot.
METRIC_NAMES = {
    "views": "views",
    "estimatedMinutesWatched": "watch_time_minutes",
    "averageViewDuration": "average_view_duration_seconds",
    "averageViewPercentage": "average_view_percentage",
    "likes": "likes",
    "dislikes": "dislikes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
    "subscribersLost": "subscribers_lost",
    "impressions": "impressions",
    "impressionClickThroughRate": "click_through_rate",
    "estimatedRevenue": "estimated_revenue",
    "estimatedAdRevenue": "estimated_ad_revenue",
    "cpm": "cpm",
    "playbackBasedCpm": "playback_cpm",
}


@analytics_registry.register
class YouTubeAnalyticsProvider(AnalyticsProvider):
    name = "youtube_analytics"

    def availability(self) -> Availability:
        """Whether the OAuth *application* exists. Per-channel consent is checked when
        a connection is used, not here."""
        missing = []
        if not settings.youtube_client_id:
            missing.append("YOUTUBE_CLIENT_ID")
        if not settings.youtube_client_secret:
            missing.append("YOUTUBE_CLIENT_SECRET")
        if missing:
            return Availability.not_configured(
                provider=self.name,
                missing=tuple(missing),
                detail=(
                    "Channel analytics require the channel owner's OAuth consent. "
                    "Configure the Google OAuth application first."
                ),
            )
        return Availability.available(
            provider=self.name,
            detail="Configured. Each channel still needs its owner's analytics consent.",
        )

    # ------------------------------------------------------------------- reports
    def channel_metrics(
        self, access_token: str, *, channel_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult:
        return self._report(
            access_token,
            channel_id=channel_id,
            start=start,
            end=end,
            filters=None,
        )

    def video_metrics(
        self, access_token: str, *, channel_id: str, video_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult:
        return self._report(
            access_token,
            channel_id=channel_id,
            start=start,
            end=end,
            filters=f"video=={video_id}",
        )

    def revenue_metrics(
        self, access_token: str, *, channel_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult | None:
        """Monetary metrics, or ``None`` when the scope was not granted.

        ``None`` is the only honest answer without the monetary scope. Revenue is never
        estimated, interpolated or derived from views — a figure NEXORA shows as
        earnings is one YouTube reported.
        """
        try:
            payload = self._request(
                access_token,
                channel_id=channel_id,
                start=start,
                end=end,
                metrics=MONETARY_METRICS,
            )
        except ProviderUnavailable as exc:
            # 403 here means the monetary scope is absent or the channel is not in the
            # Partner Programme. Either way there is no revenue figure to report.
            logger.info(
                "analytics.revenue_unavailable",
                extra={"channel_id": channel_id, "detail": str(exc)[:200]},
            )
            return None

        metrics, unavailable = _extract(payload, MONETARY_METRICS)
        if not metrics:
            return None
        return AnalyticsResult(
            source=SOURCE,
            captured_at=datetime.now(start.tzinfo),
            metrics=metrics,
            unavailable=unavailable,
            period_start=start,
            period_end=end,
            raw=payload,
        )

    # ------------------------------------------------------------------- helpers
    def _report(
        self,
        access_token: str,
        *,
        channel_id: str,
        start: datetime,
        end: datetime,
        filters: str | None,
    ) -> AnalyticsResult:
        payload = self._request(
            access_token,
            channel_id=channel_id,
            start=start,
            end=end,
            metrics=CORE_METRICS,
            filters=filters,
        )
        metrics, unavailable = _extract(payload, CORE_METRICS)

        # Impressions and CTR are a separate report. A failure here costs those two
        # metrics and nothing else, and they are reported as unavailable rather than
        # quietly missing.
        try:
            impressions = self._request(
                access_token,
                channel_id=channel_id,
                start=start,
                end=end,
                metrics=IMPRESSION_METRICS,
                filters=filters,
            )
        except ProviderUnavailable:
            unavailable.extend(METRIC_NAMES[name] for name in IMPRESSION_METRICS)
        else:
            extra, missing = _extract(impressions, IMPRESSION_METRICS)
            metrics.update(extra)
            unavailable.extend(missing)

        return AnalyticsResult(
            source=SOURCE,
            captured_at=datetime.now(start.tzinfo),
            metrics=metrics,
            unavailable=sorted(set(unavailable)),
            period_start=start,
            period_end=end,
            raw=payload,
        )

    def _request(
        self,
        access_token: str,
        *,
        channel_id: str,
        start: datetime,
        end: datetime,
        metrics: tuple[str, ...],
        filters: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ids": f"channel=={channel_id}",
            "startDate": start.date().isoformat(),
            "endDate": end.date().isoformat(),
            "metrics": ",".join(metrics),
        }
        if filters:
            params["filters"] = filters

        with http_client(timeout=60.0) as client:
            return request_json(
                client,
                "GET",
                API_ROOT,
                provider="YouTube Analytics API",
                headers={"authorization": f"Bearer {access_token}"},
                params=params,
            )


def _extract(
    payload: dict[str, Any], requested: tuple[str, ...]
) -> tuple[dict[str, Any], list[str]]:
    """Map the column-header/row table back to named metrics.

    A metric that was requested but is absent from the response — or present with a
    null value — is returned as unavailable. It is never defaulted to 0, because
    "nobody watched" and "we were not told" are different facts and the UI renders
    them differently.
    """
    headers = [str(column.get("name", "")) for column in payload.get("columnHeaders") or []]
    rows = payload.get("rows") or []

    metrics: dict[str, Any] = {}
    if rows and isinstance(rows[0], list):
        for index, name in enumerate(headers):
            if index >= len(rows[0]):
                continue
            value = rows[0][index]
            if value is None or name not in METRIC_NAMES:
                continue
            metrics[METRIC_NAMES[name]] = value

    unavailable = [
        METRIC_NAMES[name]
        for name in requested
        if name in METRIC_NAMES and METRIC_NAMES[name] not in metrics
    ]
    return metrics, unavailable
