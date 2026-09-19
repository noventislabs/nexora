"""Trend source management: creation, validation, health and scan scheduling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, NotFound, ValidationError
from nexora.db.models import TrendSource
from nexora.db.models.enums import ComponentStatus, TrendSourceKind
from nexora.services.providers.base import Availability
from nexora.services.providers.trends import build_provider

#: Backoff applied after consecutive failures, so a broken source is not hammered.
FAILURE_BACKOFF_MINUTES = (5, 15, 60, 240)
MAX_BACKOFF_MINUTES = 720

#: Seed sources for a new channel. Feeds are publisher-provided and intended for
#: syndication; nothing here scrapes a website.
DEFAULT_RSS_SOURCES = [
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "category": "technology",
        "reliability": 0.85,
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "technology",
        "reliability": 0.8,
    },
    {
        "name": "NASA Breaking News",
        "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "category": "science",
        "reliability": 0.9,
    },
    {
        "name": "Nature News",
        "url": "https://www.nature.com/nature.rss",
        "category": "science",
        "reliability": 0.9,
    },
]


def validate_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate a source configuration by constructing its provider.

    The provider owns its own validation rules, so this cannot drift from what the
    provider actually accepts.
    """
    if kind not in {member.value for member in TrendSourceKind}:
        raise ValidationError(
            f"Unknown trend source kind '{kind}'. "
            f"Supported: {', '.join(member.value for member in TrendSourceKind)}."
        )
    provider = build_provider(kind, config)
    availability = provider.availability()
    if availability.status is ComponentStatus.UNAVAILABLE:
        raise ValidationError(availability.detail or "This source configuration is not usable.")
    # Touch the validating properties so a bad value fails now rather than at scan time.
    if kind == TrendSourceKind.RSS.value:
        from nexora.services.providers.trends.rss import validate_feed_url

        config = {**config, "url": validate_feed_url(str(config.get("url", "")))}
    elif kind == TrendSourceKind.YOUTUBE_DATA_API.value:
        _ = provider.mode, provider.region_code  # type: ignore[attr-defined]
    elif kind == TrendSourceKind.REDDIT.value:
        _ = provider.subreddit, provider.listing  # type: ignore[attr-defined]
    return config


def create_source(
    session: Session,
    *,
    channel_id: uuid.UUID,
    kind: str,
    name: str,
    config: dict[str, Any],
    enabled: bool = True,
    reliability: float | None = None,
    region: str | None = None,
    min_interval_minutes: int = 60,
) -> TrendSource:
    source_name = (name or "").strip()
    if not source_name:
        raise ValidationError("Source name is required.")
    if min_interval_minutes < 5:
        raise ValidationError(
            "Minimum scan interval must be at least 5 minutes to respect upstream quotas."
        )

    cleaned = validate_config(kind, config or {})
    existing = session.execute(
        select(TrendSource).where(
            TrendSource.channel_id == channel_id,
            TrendSource.kind == kind,
            TrendSource.name == source_name,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"A {kind} source named '{source_name}' already exists for this channel.")

    source = TrendSource(
        channel_id=channel_id,
        kind=kind,
        name=source_name[:160],
        config=cleaned,
        enabled=enabled,
        reliability=_validated_reliability(reliability, cleaned),
        region=region,
        min_interval_minutes=min_interval_minutes,
    )
    session.add(source)
    session.flush()
    return source


def _validated_reliability(reliability: float | None, config: dict[str, Any]) -> float:
    value = reliability if reliability is not None else config.get("reliability", 0.7)
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Reliability must be a number between 0 and 1.") from exc
    if not 0.0 <= numeric <= 1.0:
        raise ValidationError("Reliability must be between 0 and 1.")
    return numeric


def get_source(session: Session, channel_id: uuid.UUID, source_id: uuid.UUID) -> TrendSource:
    source = session.get(TrendSource, source_id)
    if source is None or source.channel_id != channel_id:
        raise NotFound("Trend source not found.")
    return source


def list_sources(session: Session, channel_id: uuid.UUID) -> list[TrendSource]:
    return list(
        session.execute(
            select(TrendSource)
            .where(TrendSource.channel_id == channel_id)
            .order_by(TrendSource.kind.asc(), TrendSource.name.asc())
        ).scalars()
    )


def seed_default_sources(session: Session, channel_id: uuid.UUID) -> list[TrendSource]:
    """Create the starter RSS sources for a new channel.

    Only RSS is seeded: it needs no credentials, so the channel has a working trend
    source on day one without the system pretending an unconfigured API is available.
    """
    created = []
    for spec in DEFAULT_RSS_SOURCES:
        try:
            created.append(
                create_source(
                    session,
                    channel_id=channel_id,
                    kind=TrendSourceKind.RSS.value,
                    name=str(spec["name"]),
                    config={
                        "url": spec["url"],
                        "category": spec["category"],
                        "language": "en",
                    },
                    reliability=float(spec["reliability"]),
                    region="GLOBAL",
                )
            )
        except Conflict:
            continue
    return created


def source_availability(source: TrendSource) -> Availability:
    """Live availability for a configured source, from its provider."""
    try:
        provider = build_provider(source.kind, {**source.config, "reliability": source.reliability})
    except Exception as exc:
        return Availability.unavailable(provider=source.kind, detail=str(exc))
    if not source.enabled:
        return Availability(
            status=ComponentStatus.NOT_CONNECTED,
            provider=source.kind,
            detail="This source is disabled.",
        )
    try:
        return provider.availability()
    except Exception as exc:
        return Availability.unavailable(provider=source.kind, detail=str(exc))


def is_due(source: TrendSource, *, now: datetime | None = None) -> bool:
    """Whether the bounded-caching window allows fetching this source again."""
    now = now or datetime.now(UTC)
    if source.next_allowed_at is not None and source.next_allowed_at > now:
        return False
    if source.last_run_at is None:
        return True
    return source.last_run_at + timedelta(minutes=source.min_interval_minutes) <= now


def next_allowed_after_success(source: TrendSource, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(minutes=source.min_interval_minutes)


def next_allowed_after_failure(source: TrendSource, *, now: datetime | None = None) -> datetime:
    """Back off *on top of* the configured interval.

    Taking the max of the two would let a long interval swallow the backoff entirely,
    so a repeatedly failing source would be retried on the same cadence as a healthy
    one. Adding them keeps the delay strictly increasing with each failure.
    """
    now = now or datetime.now(UTC)
    index = min(source.consecutive_failures, len(FAILURE_BACKOFF_MINUTES)) - 1
    backoff = (
        FAILURE_BACKOFF_MINUTES[index]
        if 0 <= index < len(FAILURE_BACKOFF_MINUTES)
        else MAX_BACKOFF_MINUTES
    )
    return now + timedelta(minutes=min(source.min_interval_minutes + backoff, MAX_BACKOFF_MINUTES))


def source_to_dict(source: TrendSource, *, availability: Availability | None = None) -> dict[str, Any]:
    resolved = availability or source_availability(source)
    return {
        "id": str(source.id),
        "kind": source.kind,
        "name": source.name,
        "config": _redacted_config(source.config),
        "enabled": source.enabled,
        "reliability": source.reliability,
        "region": source.region,
        "min_interval_minutes": source.min_interval_minutes,
        "last_run_at": source.last_run_at.isoformat() if source.last_run_at else None,
        "last_status": source.last_status,
        "last_error": source.last_error,
        "last_item_count": source.last_item_count,
        "consecutive_failures": source.consecutive_failures,
        "next_allowed_at": source.next_allowed_at.isoformat() if source.next_allowed_at else None,
        "due_now": is_due(source),
        "availability": resolved.to_dict(),
    }


def _redacted_config(config: dict[str, Any]) -> dict[str, Any]:
    """Source configs hold no credentials today; this guards against that changing."""
    redacted = {}
    for key, value in (config or {}).items():
        if any(marker in key.lower() for marker in ("key", "secret", "token", "password")):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted
