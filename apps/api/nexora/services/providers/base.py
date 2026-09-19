"""Provider interfaces shared by every external integration.

Design rule for this package: a provider either does the real thing or reports that it
cannot. There is no third branch that returns invented data. When credentials are
missing, :meth:`Provider.availability` returns ``NOT CONFIGURED`` and callers surface
that state verbatim to the operator.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from nexora.core.errors import ProviderNotConfigured
from nexora.db.models.enums import ComponentStatus


@dataclass(frozen=True)
class Availability:
    """Whether a provider can actually be used right now."""

    status: ComponentStatus
    provider: str | None = None
    detail: str = ""
    missing_settings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return self.status not in (
            ComponentStatus.NOT_CONFIGURED,
            ComponentStatus.NOT_CONNECTED,
        )

    @property
    def usable(self) -> bool:
        return self.status in (
            ComponentStatus.AVAILABLE,
            ComponentStatus.HEALTHY,
            ComponentStatus.CONNECTED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "detail": self.detail,
            "missing_settings": list(self.missing_settings),
            "metadata": self.metadata,
        }

    @classmethod
    def not_configured(
        cls, *, provider: str | None = None, missing: tuple[str, ...] = (), detail: str = ""
    ) -> Availability:
        if not detail:
            joined = ", ".join(missing) if missing else "credentials"
            detail = f"Set {joined} to enable this integration."
        return cls(
            status=ComponentStatus.NOT_CONFIGURED,
            provider=provider,
            detail=detail,
            missing_settings=missing,
        )

    @classmethod
    def available(cls, *, provider: str, detail: str = "", **metadata: Any) -> Availability:
        return cls(
            status=ComponentStatus.AVAILABLE, provider=provider, detail=detail, metadata=metadata
        )

    @classmethod
    def unavailable(cls, *, provider: str | None = None, detail: str = "") -> Availability:
        return cls(status=ComponentStatus.UNAVAILABLE, provider=provider, detail=detail)


class Provider(abc.ABC):
    """Base class for every external integration."""

    #: Stable identifier used in configuration, the database and the UI.
    name: str = "unknown"
    #: Human-readable kind, e.g. ``"voice"``.
    kind: str = "provider"

    @abc.abstractmethod
    def availability(self) -> Availability:
        """Report whether this provider is configured and reachable.

        This must be cheap and must not perform network calls; use
        :meth:`health_check` for anything that talks to the network.
        """

    def health_check(self) -> Availability:
        """Optionally verify the integration end to end. Defaults to :meth:`availability`."""
        return self.availability()

    def require(self) -> None:
        """Raise :class:`ProviderNotConfigured` unless this provider can be used."""
        availability = self.availability()
        if not availability.configured:
            raise ProviderNotConfigured(
                f"{self.kind} provider is NOT CONFIGURED. {availability.detail}".strip(),
                details=availability.to_dict(),
            )


P = TypeVar("P", bound=Provider)


class ProviderRegistry(Generic[P]):
    """Name → provider-factory registry with an optional configured default.

    Registries are populated at import time by each provider module, so adding a
    provider never requires editing call sites.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self._factories: dict[str, type[P]] = {}

    def register(self, provider_cls: type[P]) -> type[P]:
        self._factories[provider_cls.name] = provider_cls
        return provider_cls

    def names(self) -> list[str]:
        return sorted(self._factories)

    def has(self, name: str) -> bool:
        return name in self._factories

    def create(self, name: str) -> P:
        try:
            return self._factories[name]()
        except KeyError:
            raise ProviderNotConfigured(
                f"Unknown {self.kind} provider '{name}'. "
                f"Available: {', '.join(self.names()) or 'none'}.",
                details={"kind": self.kind, "requested": name, "available": self.names()},
            ) from None

    def all(self) -> list[P]:
        return [factory() for factory in self._factories.values()]
