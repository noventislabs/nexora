"""Analytics providers.

The YouTube Analytics implementation lives with the YouTube provider because it shares
the same OAuth connection; this package re-exports the registry for symmetry.
"""

# Importing the implementation registers it on the registry.
from nexora.services.providers.analytics.youtube import YouTubeAnalyticsProvider
from nexora.services.providers.youtube.base import (
    AnalyticsProvider,
    AnalyticsResult,
    analytics_registry,
)

__all__ = [
    "AnalyticsProvider",
    "AnalyticsResult",
    "YouTubeAnalyticsProvider",
    "analytics_registry",
]
