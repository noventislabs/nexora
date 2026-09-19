"""YouTubeProvider and AnalyticsProvider interfaces.

Authentication is OAuth 2.0 only. NEXORA never requests, transports or stores a
YouTube account password.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO

from nexora.services.providers.base import Provider, ProviderRegistry


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: tuple[str, ...]
    token_type: str = "Bearer"


@dataclass(frozen=True)
class RemoteChannel:
    id: str
    title: str
    custom_url: str | None = None
    subscriber_count: int | None = None
    subscriber_count_hidden: bool = False
    video_count: int | None = None
    view_count: int | None = None
    uploads_playlist_id: str | None = None


@dataclass(frozen=True)
class UploadResult:
    video_id: str
    upload_status: str
    privacy_status: str | None
    published_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteVideo:
    id: str
    title: str
    description: str | None
    tags: list[str]
    privacy_status: str | None
    upload_status: str | None
    published_at: datetime | None
    duration_seconds: int | None
    thumbnail_url: str | None
    statistics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class YouTubeProvider(Provider, abc.ABC):
    """Write/read operations against a connected YouTube channel."""

    kind = "youtube"

    @abc.abstractmethod
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    @abc.abstractmethod
    def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokens: ...

    @abc.abstractmethod
    def refresh_tokens(self, refresh_token: str) -> OAuthTokens: ...

    @abc.abstractmethod
    def revoke(self, token: str) -> None: ...

    @abc.abstractmethod
    def get_my_channel(self, access_token: str) -> RemoteChannel: ...

    @abc.abstractmethod
    def upload_video(
        self,
        access_token: str,
        *,
        media: BinaryIO,
        media_size: int,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        category_id: str | None = None,
        publish_at: datetime | None = None,
        made_for_kids: bool = False,
        language: str = "en",
    ) -> UploadResult: ...

    @abc.abstractmethod
    def set_thumbnail(self, access_token: str, *, video_id: str, image: bytes, mime_type: str) -> None: ...

    @abc.abstractmethod
    def get_video(self, access_token: str, video_id: str) -> RemoteVideo | None: ...

    @abc.abstractmethod
    def list_channel_videos(self, access_token: str, *, limit: int = 25) -> list[RemoteVideo]: ...


@dataclass(frozen=True)
class AnalyticsResult:
    """A raw analytics reading.

    ``metrics`` contains only what the API returned. ``unavailable`` names the metrics
    that were requested but not granted or not returned, so the UI can say
    "unavailable" instead of showing a zero.
    """

    source: str
    captured_at: datetime
    metrics: dict[str, Any]
    unavailable: list[str] = field(default_factory=list)
    period_start: datetime | None = None
    period_end: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AnalyticsProvider(Provider, abc.ABC):
    kind = "analytics"

    @abc.abstractmethod
    def channel_metrics(
        self, access_token: str, *, channel_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult: ...

    @abc.abstractmethod
    def video_metrics(
        self, access_token: str, *, channel_id: str, video_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult: ...

    @abc.abstractmethod
    def revenue_metrics(
        self, access_token: str, *, channel_id: str, start: datetime, end: datetime
    ) -> AnalyticsResult | None:
        """Monetary metrics, or ``None`` when the granted scopes do not include them.

        Returning ``None`` is the only honest answer without the monetary scope; a
        revenue figure is never estimated or interpolated.
        """


youtube_registry: ProviderRegistry[YouTubeProvider] = ProviderRegistry("youtube")
analytics_registry: ProviderRegistry[AnalyticsProvider] = ProviderRegistry("analytics")
