"""Trend providers. Importing this package registers every source kind."""

from nexora.services.providers.trends import reddit as _reddit  # noqa: F401
from nexora.services.providers.trends import rss as _rss  # noqa: F401
from nexora.services.providers.trends import youtube as _youtube  # noqa: F401
from nexora.services.providers.trends.base import (
    NormalizedTrend,
    TrendFetchResult,
    TrendProvider,
    trend_registry,
)

__all__ = [
    "NormalizedTrend",
    "TrendFetchResult",
    "TrendProvider",
    "build_provider",
    "trend_registry",
]


def build_provider(kind: str, config: dict | None = None) -> TrendProvider:
    """Instantiate a trend provider by its ``trend_sources.kind`` value."""
    provider_cls = type(trend_registry.create(kind))
    return provider_cls(config or {})
