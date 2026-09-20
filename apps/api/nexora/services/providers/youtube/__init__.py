"""YouTube providers. Importing this package registers the Google implementation."""

from nexora.services.providers.youtube import google as _google  # noqa: F401
from nexora.services.providers.youtube.base import (
    AnalyticsProvider,
    AnalyticsResult,
    OAuthTokens,
    RemoteChannel,
    RemoteVideo,
    UploadResult,
    YouTubeProvider,
    analytics_registry,
    youtube_registry,
)

__all__ = [
    "AnalyticsProvider",
    "AnalyticsResult",
    "OAuthTokens",
    "RemoteChannel",
    "RemoteVideo",
    "UploadResult",
    "YouTubeProvider",
    "analytics_registry",
    "get_youtube",
    "youtube_registry",
]


def get_youtube(*, include_analytics: bool = True, include_monetary: bool = False):
    """The YouTube provider, raising ProviderNotConfigured without OAuth credentials."""
    from nexora.services.providers.youtube.google import GoogleYouTubeProvider

    provider = GoogleYouTubeProvider(
        include_analytics=include_analytics, include_monetary=include_monetary
    )
    provider.require()
    return provider
