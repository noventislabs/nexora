"""TrendProvider interface and the normalized trend schema.

Every source is reduced to :class:`NormalizedTrend`. Fields a source does not supply
stay ``None`` — never ``0`` — so downstream scoring can tell "no data" from "zero".
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nexora.services.providers.base import Provider, ProviderRegistry


@dataclass
class NormalizedTrend:
    external_id: str
    title: str
    url: str | None = None
    summary: str | None = None
    category: str | None = None
    language: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    #: Sparse: only keys the source actually returned (views, likes, comments, score...).
    engagement: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def dedupe_hash(self, source_name: str) -> str:
        basis = f"{source_name}|{self.external_id}".encode()
        return hashlib.sha256(basis).hexdigest()


@dataclass
class TrendFetchResult:
    items: list[NormalizedTrend]
    fetched_at: datetime
    source_name: str
    provider: str
    warnings: list[str] = field(default_factory=list)


class TrendProvider(Provider, abc.ABC):
    kind = "trend"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    @abc.abstractmethod
    def fetch(self, *, limit: int = 50) -> TrendFetchResult:
        """Fetch current items from this source. Never fabricates items on failure."""

    @property
    def reliability(self) -> float:
        """Source-reliability weight in [0, 1] used by the Opportunity Score."""
        value = self.config.get("reliability")
        return float(value) if isinstance(value, int | float) else 0.7


trend_registry: ProviderRegistry[TrendProvider] = ProviderRegistry("trend")
