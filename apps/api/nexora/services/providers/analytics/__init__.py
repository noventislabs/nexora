"""Analytics providers.

The YouTube Analytics implementation lives with the YouTube provider because it shares
the same OAuth connection; this package re-exports the registry for symmetry.
"""

from nexora.services.providers.youtube.base import (
    AnalyticsProvider,
    AnalyticsResult,
    analytics_registry,
)

__all__ = ["AnalyticsProvider", "AnalyticsResult", "analytics_registry"]
