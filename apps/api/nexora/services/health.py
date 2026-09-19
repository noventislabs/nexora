"""System health. Every status here comes from an actual probe or an actual setting.

Nothing in this module reports HEALTHY by assumption: PostgreSQL and Redis are
round-tripped, FFmpeg is executed, and provider statuses are read from configuration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from nexora.core.logging import get_logger
from nexora.db.models.enums import ComponentStatus
from nexora.services import availability as avail
from nexora.services.ffmpeg_runtime import ffmpeg_info

logger = get_logger(__name__)


@dataclass
class ComponentHealth:
    name: str
    status: ComponentStatus
    detail: str = ""
    latency_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 2) if self.latency_ms is not None else None,
            "metadata": self.metadata,
        }


def check_database() -> ComponentHealth:
    from nexora.db.session import get_engine

    started = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SHOW server_version")).scalar_one()
            migration = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar_one_or_none()
    except Exception as exc:
        return ComponentHealth(
            name="database",
            status=ComponentStatus.UNHEALTHY,
            detail=f"PostgreSQL is not reachable: {type(exc).__name__}: {exc}",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    latency = (time.perf_counter() - started) * 1000
    status = ComponentStatus.HEALTHY if migration else ComponentStatus.DEGRADED
    detail = f"PostgreSQL {version}"
    if not migration:
        detail += " — migrations have not been applied (run `alembic upgrade head`)"
    return ComponentHealth(
        name="database",
        status=status,
        detail=detail,
        latency_ms=latency,
        metadata={"server_version": version, "migration_revision": migration},
    )


def check_queue() -> ComponentHealth:
    from nexora.queue.broker import queue_health

    return queue_health()


def check_ffmpeg() -> ComponentHealth:
    info = ffmpeg_info()
    return ComponentHealth(
        name="ffmpeg",
        status=ComponentStatus.AVAILABLE if info.available else ComponentStatus.UNAVAILABLE,
        detail=info.detail,
        metadata={"version": info.version, "path": info.ffmpeg_path, "ffprobe": info.ffprobe_path},
    )


def _from_availability(name: str, availability: Any) -> ComponentHealth:
    return ComponentHealth(
        name=name,
        status=availability.status,
        detail=availability.detail,
        metadata={
            "provider": availability.provider,
            "missing_settings": list(availability.missing_settings),
            **availability.metadata,
        },
    )


def check_storage() -> ComponentHealth:
    return _from_availability("storage", avail.storage_availability())


def check_llm() -> ComponentHealth:
    return _from_availability("llm_provider", avail.llm_availability())


def check_voice() -> ComponentHealth:
    return _from_availability("voice_provider", avail.voice_availability())


def check_youtube() -> ComponentHealth:
    return _from_availability("youtube", avail.youtube_oauth_availability())


def check_encryption() -> ComponentHealth:
    return _from_availability("encryption", avail.encryption_availability())


#: Components that make the whole system unusable when broken.
CRITICAL = {"database", "queue"}


def system_health() -> dict[str, Any]:
    components = [
        check_database(),
        check_queue(),
        check_storage(),
        check_ffmpeg(),
        check_youtube(),
        check_llm(),
        check_voice(),
        check_encryption(),
    ]
    degraded = any(
        component.status in (ComponentStatus.UNHEALTHY, ComponentStatus.DEGRADED)
        for component in components
        if component.name in CRITICAL
    )
    unhealthy = any(
        component.status is ComponentStatus.UNHEALTHY
        for component in components
        if component.name in CRITICAL
    )
    overall = (
        ComponentStatus.UNHEALTHY
        if unhealthy
        else ComponentStatus.DEGRADED
        if degraded
        else ComponentStatus.HEALTHY
    )
    return {
        "status": overall.value,
        "checked_at": datetime.now(UTC).isoformat(),
        "components": [component.to_dict() for component in components],
    }
