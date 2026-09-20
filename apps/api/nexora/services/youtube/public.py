"""Public YouTube reads using the server API key.

This is a genuinely different capability from an OAuth connection, and the code keeps
them apart:

* A **public read** identifies a channel and returns the statistics YouTube shows any
  visitor. It needs only ``YOUTUBE_API_KEY``. It cannot upload, and it cannot see
  impressions, click-through rate, watch time or revenue.
* An **OAuth connection** is consent from the channel owner. Only that enables
  uploading and the Analytics API.

Verifying a channel publicly therefore never sets the connection status to CONNECTED.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import NotFound, ProviderNotConfigured, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import AnalyticsSnapshot, Channel, YouTubeConnection
from nexora.db.models.enums import ActorType, AnalyticsScope
from nexora.services import audit
from nexora.services.http import http_client, request_json

logger = get_logger(__name__)

API_ROOT = "https://www.googleapis.com/youtube/v3"
SOURCE = "youtube_data_api_public"

#: Metrics an OAuth connection plus the Analytics API would add. Listed explicitly so
#: the UI can say what is missing and why, rather than showing blanks.
METRICS_REQUIRING_OAUTH = (
    "impressions",
    "click_through_rate",
    "watch_time_minutes",
    "average_view_duration_seconds",
    "average_view_percentage",
    "estimated_revenue",
)


@dataclass
class PublicChannel:
    id: str
    title: str
    custom_url: str | None
    description: str | None
    published_at: datetime | None
    country: str | None
    subscriber_count: int | None
    subscriber_count_hidden: bool
    view_count: int | None
    video_count: int | None
    uploads_playlist_id: str | None
    thumbnail_url: str | None

    def metrics(self) -> dict[str, Any]:
        """Only what the API actually returned. A hidden count is absent, not zero."""
        metrics: dict[str, Any] = {}
        if self.subscriber_count is not None:
            metrics["subscriber_count"] = self.subscriber_count
        if self.view_count is not None:
            metrics["view_count"] = self.view_count
        if self.video_count is not None:
            metrics["video_count"] = self.video_count
        return metrics

    def unavailable_metrics(self) -> list[str]:
        missing = list(METRICS_REQUIRING_OAUTH)
        if self.subscriber_count is None:
            missing.append("subscriber_count")
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.id,
            "title": self.title,
            "custom_url": self.custom_url,
            "description": self.description,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "country": self.country,
            "subscriber_count": self.subscriber_count,
            "subscriber_count_hidden": self.subscriber_count_hidden,
            "view_count": self.view_count,
            "video_count": self.video_count,
            "thumbnail_url": self.thumbnail_url,
            "source": SOURCE,
            "scope_note": (
                "Public data only. Impressions, click-through rate, watch time, retention "
                "and revenue require an OAuth connection from the channel owner."
            ),
        }


def require_api_key() -> str:
    if not settings.youtube_api_key:
        raise ProviderNotConfigured(
            "YouTube Data API key is NOT CONFIGURED. Set YOUTUBE_API_KEY to read public "
            "channel data.",
            details={"missing_settings": ["YOUTUBE_API_KEY"]},
        )
    return settings.youtube_api_key


def normalize_channel_id(value: str) -> str:
    """Accept a channel id, a /channel/ URL, or a bare id without the UC prefix."""
    candidate = (value or "").strip()
    if not candidate:
        raise ValidationError("A YouTube channel id is required.")
    if "/channel/" in candidate:
        candidate = candidate.split("/channel/", 1)[1].split("/")[0].split("?")[0]
    if candidate.startswith("@"):
        raise ValidationError(
            "A handle such as @name is not a channel id. Use the UC… id, which appears in "
            "YouTube Studio under Settings → Channel → Advanced settings."
        )
    # A YouTube user id is the channel id without its two-character prefix.
    if not candidate.startswith("UC") and len(candidate) == 22:
        candidate = f"UC{candidate}"
    if not candidate.startswith("UC") or len(candidate) != 24:
        raise ValidationError(
            f"'{value}' is not a YouTube channel id. A channel id starts with 'UC' and is "
            "24 characters long."
        )
    return candidate


def fetch_public_channel(channel_id: str) -> PublicChannel:
    """Read a channel's public profile and statistics."""
    key = require_api_key()
    resolved = normalize_channel_id(channel_id)

    with http_client() as client:
        payload = request_json(
            client,
            "GET",
            f"{API_ROOT}/channels",
            provider="YouTube Data API",
            params={"part": "snippet,statistics,contentDetails", "id": resolved, "key": key},
        )

    items = payload.get("items") or []
    if not items:
        raise NotFound(
            f"No public YouTube channel exists with id {resolved}. Check the id in YouTube "
            "Studio under Settings → Channel → Advanced settings."
        )

    item = items[0]
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    hidden = bool(statistics.get("hiddenSubscriberCount"))
    thumbnails = snippet.get("thumbnails") or {}

    return PublicChannel(
        id=str(item.get("id", resolved)),
        title=str(snippet.get("title", "")),
        custom_url=snippet.get("customUrl"),
        description=snippet.get("description"),
        published_at=_parse_iso(snippet.get("publishedAt")),
        country=snippet.get("country"),
        subscriber_count=None if hidden else _as_int(statistics.get("subscriberCount")),
        subscriber_count_hidden=hidden,
        view_count=_as_int(statistics.get("viewCount")),
        video_count=_as_int(statistics.get("videoCount")),
        uploads_playlist_id=(item.get("contentDetails") or {})
        .get("relatedPlaylists", {})
        .get("uploads"),
        thumbnail_url=((thumbnails.get("high") or thumbnails.get("default")) or {}).get("url"),
    )


def link_public_channel(
    session: Session,
    channel: Channel,
    channel_id: str,
    *,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> tuple[YouTubeConnection, PublicChannel]:
    """Verify a channel id publicly and record it — without claiming OAuth consent."""
    public = fetch_public_channel(channel_id)

    connection = session.execute(
        select(YouTubeConnection).where(YouTubeConnection.channel_id == channel.id)
    ).scalar_one_or_none()
    if connection is None:
        connection = YouTubeConnection(channel_id=channel.id, scopes=[])
        session.add(connection)

    connection.public_channel_id = public.id
    connection.public_channel_title = public.title
    connection.public_verified_at = datetime.now(UTC)
    # Deliberately untouched: status stays NOT_CONNECTED until OAuth consent exists.
    session.flush()

    snapshot = record_public_snapshot(session, channel, public)

    audit.record(
        session,
        action="youtube.public_channel_linked",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="youtube_connection",
        entity_id=connection.id,
        summary=(
            f"Linked public channel {public.id} ('{public.title}'). This is a public read "
            "only — uploading still requires an OAuth connection."
        ),
        after={"snapshot_id": str(snapshot.id), **public.to_dict()},
    )
    logger.info(
        "youtube.public_channel_linked",
        extra={"channel_id": str(channel.id), "youtube_channel_id": public.id},
    )
    return connection, public


def record_public_snapshot(
    session: Session, channel: Channel, public: PublicChannel
) -> AnalyticsSnapshot:
    """Store what the public API returned, marking what it cannot cover."""
    snapshot = AnalyticsSnapshot(
        channel_id=channel.id,
        scope=AnalyticsScope.CHANNEL.value,
        source=SOURCE,
        captured_at=datetime.now(UTC),
        metrics=public.metrics(),
        unavailable_metrics=public.unavailable_metrics(),
        raw=public.to_dict(),
        # Public data is never the whole picture.
        is_complete=False,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def public_status(session: Session, channel: Channel) -> dict[str, Any]:
    connection = session.execute(
        select(YouTubeConnection).where(YouTubeConnection.channel_id == channel.id)
    ).scalar_one_or_none()
    if connection is None or not connection.public_channel_id:
        return {
            "linked": False,
            "channel_id": None,
            "title": None,
            "verified_at": None,
            "note": "No public channel has been linked yet.",
        }
    return {
        "linked": True,
        "channel_id": connection.public_channel_id,
        "title": connection.public_channel_title,
        "verified_at": (
            connection.public_verified_at.isoformat() if connection.public_verified_at else None
        ),
        "note": (
            "Public read only. Uploading and full analytics require an OAuth connection "
            "from the channel owner."
        ),
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
