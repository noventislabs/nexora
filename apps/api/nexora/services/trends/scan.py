"""Trend scanning: fetch → normalize → deduplicate → persist → score.

Every number that lands in the database came from an upstream response. When a source
fails, the failure is recorded on that source and the scan continues with the others —
a broken feed degrades coverage, it never produces substitute data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NexoraError, ProviderNotConfigured, RateLimited
from nexora.core.logging import get_logger
from nexora.db.models import Channel, TrendingTopic, TrendSource
from nexora.db.models.enums import ActorType, SourceScope
from nexora.services import audit
from nexora.services.providers.trends import build_provider
from nexora.services.providers.trends.base import NormalizedTrend
from nexora.services.trends import sources as source_service
from nexora.services.trends.scoring import ScanContext, score_trend

logger = get_logger(__name__)

#: How long a stored trend is considered current. Older rows are labelled STALE rather
#: than silently presented as "trending now".
FRESHNESS_WINDOW = timedelta(hours=48)

#: Window used when looking for an earlier row describing the same story.
DEDUPE_LOOKBACK = timedelta(days=7)


@dataclass
class SourceScanResult:
    source_id: uuid.UUID
    source_name: str
    kind: str
    status: str  # SUCCESS | FAILED | SKIPPED | NOT_CONFIGURED
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    #: Items this source returned, carried to the persist step. Never serialized.
    items: list[NormalizedTrend] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "kind": self.kind,
            "status": self.status,
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "error": self.error,
            "warnings": self.warnings,
        }


@dataclass
class ScanResult:
    channel_id: uuid.UUID
    started_at: datetime
    finished_at: datetime
    sources: list[SourceScanResult] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return sum(result.stored for result in self.sources)

    @property
    def fetched(self) -> int:
        return sum(result.fetched for result in self.sources)

    @property
    def duplicates(self) -> int:
        return sum(result.duplicates for result in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": str(self.channel_id),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round((self.finished_at - self.started_at).total_seconds(), 2),
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "sources": [result.to_dict() for result in self.sources],
        }


def scan_channel(
    session: Session,
    channel: Channel,
    *,
    source_ids: list[uuid.UUID] | None = None,
    force: bool = False,
    limit_per_source: int = 50,
    actor_type: ActorType = ActorType.SYSTEM,
    user_id: uuid.UUID | None = None,
) -> ScanResult:
    """Scan the enabled trend sources this channel can see.

    That is the channel's own sources plus the account's shared ones. A shared source
    is fetched once per due-interval regardless of which channel triggers the scan, so
    adding channels does not multiply the load on an upstream feed.
    """
    started = datetime.now(UTC)
    sources = [
        source
        for source in source_service.list_sources(
            session, channel.id, user_id=channel.user_id
        )
        if source.enabled and (source_ids is None or source.id in source_ids)
    ]

    results: list[SourceScanResult] = []
    collected: list[tuple[TrendSource, NormalizedTrend]] = []

    for source in sources:
        if not force and not source_service.is_due(source, now=started):
            results.append(
                SourceScanResult(
                    source_id=source.id,
                    source_name=source.name,
                    kind=source.kind,
                    status="SKIPPED",
                    error=(
                        f"Not due yet: this source is fetched at most every "
                        f"{source.min_interval_minutes} minutes."
                    ),
                )
            )
            continue

        result = _fetch_source(session, source, limit=limit_per_source, now=started)
        results.append(result)
        collected.extend((source, trend) for trend in result.items)

    stored_rows = _persist(session, channel, collected, results, now=started)
    _score(session, channel, stored_rows)

    finished = datetime.now(UTC)
    scan = ScanResult(
        channel_id=channel.id, started_at=started, finished_at=finished, sources=results
    )

    audit.record(
        session,
        action="trends.scan_completed",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="channel",
        entity_id=channel.id,
        summary=(
            f"Scanned {len(results)} source(s): fetched {scan.fetched}, stored {scan.stored}, "
            f"{scan.duplicates} duplicate(s)."
        ),
        after=scan.to_dict(),
    )
    return scan


def _fetch_source(
    session: Session, source: TrendSource, *, limit: int, now: datetime
) -> SourceScanResult:
    result = SourceScanResult(
        source_id=source.id, source_name=source.name, kind=source.kind, status="FAILED"
    )
    try:
        provider = build_provider(source.kind, {**source.config, "reliability": source.reliability})
        fetched = provider.fetch(limit=limit)
    except ProviderNotConfigured as exc:
        result.status = "NOT_CONFIGURED"
        result.error = exc.message
        _record_failure(source, exc.message, now=now, counts_as_failure=False)
        logger.info(
            "trends.source_not_configured",
            extra={"source_id": str(source.id), "kind": source.kind},
        )
        return result
    except RateLimited as exc:
        result.error = exc.message
        _record_failure(source, exc.message, now=now)
        logger.warning("trends.source_rate_limited", extra={"source_id": str(source.id)})
        return result
    except NexoraError as exc:
        result.error = exc.message
        _record_failure(source, exc.message, now=now)
        logger.warning(
            "trends.source_failed", extra={"source_id": str(source.id), "error": exc.message}
        )
        return result
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        result.error = message
        _record_failure(source, message, now=now)
        logger.exception("trends.source_error", extra={"source_id": str(source.id)})
        return result

    result.items = fetched.items
    result.status = "SUCCESS"
    result.fetched = len(fetched.items)
    result.warnings = fetched.warnings

    source.last_run_at = now
    source.last_status = "SUCCESS"
    source.last_error = None
    source.last_item_count = len(fetched.items)
    source.consecutive_failures = 0
    source.next_allowed_at = source_service.next_allowed_after_success(source, now=now)
    session.flush()
    return result


def _record_failure(
    source: TrendSource, message: str, *, now: datetime, counts_as_failure: bool = True
) -> None:
    source.last_run_at = now
    source.last_status = "NOT_CONFIGURED" if not counts_as_failure else "FAILED"
    source.last_error = message[:2000]
    source.last_item_count = None
    if counts_as_failure:
        source.consecutive_failures += 1
    source.next_allowed_at = source_service.next_allowed_after_failure(source, now=now)


def _persist(
    session: Session,
    channel: Channel,
    collected: list[tuple[TrendSource, NormalizedTrend]],
    results: list[SourceScanResult],
    *,
    now: datetime,
) -> list[TrendingTopic]:
    """Insert new observations, deduplicating within and across sources."""
    if not collected:
        return []

    by_source = {result.source_id: result for result in results}
    # Dedupe across everything this channel can see: its own rows plus the shared
    # rows its owner ingests. Scoping to the channel alone would store a shared story
    # twice the first time a second channel scanned the same feed.
    existing_source_keys = _existing_dedupe_hashes(session, channel)
    content_index = _existing_content_hashes(session, channel, now=now)
    stored: list[TrendingTopic] = []
    seen_in_batch: set[str] = set()

    for source, trend in collected:
        dedupe_hash = trend.dedupe_hash(source.name)
        if (source.id, dedupe_hash) in existing_source_keys or dedupe_hash in seen_in_batch:
            # The same item from the same source; nothing new happened.
            by_source[source.id].duplicates += 1
            continue
        seen_in_batch.add(dedupe_hash)

        content_hash = trend.content_hash()
        duplicate_of = content_index.get(content_hash)

        shared = source.scope == SourceScope.SHARED.value
        row = TrendingTopic(
            source_id=source.id,
            # A shared row belongs to the user, not to whichever channel happened to
            # trigger the scan, so every one of that user's channels can rank it.
            channel_id=None if shared else channel.id,
            user_id=channel.user_id,
            source_kind=source.kind,
            source_name=source.name,
            external_id=trend.external_id[:255],
            dedupe_hash=dedupe_hash,
            content_hash=content_hash,
            duplicate_of_id=duplicate_of,
            title=trend.title,
            url=trend.url,
            summary=trend.summary,
            category=trend.category,
            language=trend.language,
            author=trend.author,
            region=trend.region or source.region,
            published_at=trend.published_at,
            discovered_at=now,
            engagement=trend.engagement,
            raw=trend.raw,
        )
        session.add(row)
        session.flush()

        if duplicate_of is not None:
            # A second source telling the same story is corroboration, not noise.
            original = session.get(TrendingTopic, duplicate_of)
            if original is not None:
                original.corroboration_count += 1
            by_source[source.id].duplicates += 1
        else:
            content_index[content_hash] = row.id
            by_source[source.id].stored += 1
            stored.append(row)

    session.flush()
    return stored


def _visible_condition(channel: Channel):
    """Rows this channel can see: its own, plus its owner's shared rows.

    Never another channel's rows — mixing one audience's trends into another's is
    exactly what the per-channel relevance design exists to prevent.
    """
    return (TrendingTopic.channel_id == channel.id) | (
        TrendingTopic.channel_id.is_(None) & (TrendingTopic.user_id == channel.user_id)
    )


def _existing_dedupe_hashes(session: Session, channel: Channel) -> set[tuple[uuid.UUID, str]]:
    rows = session.execute(
        select(TrendingTopic.source_id, TrendingTopic.dedupe_hash).where(
            _visible_condition(channel)
        )
    ).all()
    return {(source_id, dedupe) for source_id, dedupe in rows}


def _existing_content_hashes(
    session: Session, channel: Channel, *, now: datetime
) -> dict[str, uuid.UUID]:
    """Map content hash → the earliest original row, within the lookback window."""
    rows = session.execute(
        select(TrendingTopic.content_hash, TrendingTopic.id)
        .where(
            _visible_condition(channel),
            TrendingTopic.discovered_at >= now - DEDUPE_LOOKBACK,
            TrendingTopic.duplicate_of_id.is_(None),
        )
        .order_by(TrendingTopic.discovered_at.asc())
    ).all()
    index: dict[str, uuid.UUID] = {}
    for content_hash, row_id in rows:
        index.setdefault(content_hash, row_id)
    return index


def _score(session: Session, channel: Channel, rows: list[TrendingTopic]) -> None:
    """Score the newly stored rows against the whole batch as peer context."""
    if not rows:
        return

    trends = [
        NormalizedTrend(
            external_id=row.external_id,
            title=row.title,
            url=row.url,
            summary=row.summary,
            category=row.category,
            language=row.language,
            author=row.author,
            region=row.region,
            published_at=row.published_at,
            engagement=row.engagement or {},
            raw=row.raw or {},
        )
        for row in rows
    ]
    context = ScanContext.build(
        trends,
        [row.source_kind for row in rows],
        source_names=[row.source_name for row in rows],
    )
    reliabilities = {
        source.id: source.reliability
        for source in source_service.list_sources(session, channel.id, user_id=channel.user_id)
    }
    now = datetime.now(UTC)

    for index, (row, trend) in enumerate(zip(rows, trends, strict=True)):
        result = score_trend(
            trend,
            source_kind=row.source_kind,
            source_reliability=reliabilities.get(row.source_id, 0.7),
            context=context,
            index=index,
            now=now,
        )
        row.signal_score = result.score
        row.signal_breakdown = result.to_dict()
        row.scored_at = now
    session.flush()

    # The signal is channel-independent, so it is computed once. Relevance is the
    # per-channel half, and it runs for every channel that can see these rows.
    _rank_for_channels(session, channel, rows)


def _rank_for_channels(
    session: Session, scanning_channel: Channel, rows: list[TrendingTopic]
) -> None:
    """Rank the new rows for every channel entitled to see them.

    A row from a channel-scoped source is ranked only by that channel. A row from a
    shared source is ranked by each of the owner's channels, separately, so a kids
    channel and a technology channel reading the same feed reach their own verdicts.
    """
    from nexora.services.trends import relevance as relevance_service

    channel_rows = [row for row in rows if row.channel_id is not None]
    shared_rows = [row for row in rows if row.channel_id is None]

    if channel_rows:
        relevance_service.recompute_for_channel(session, scanning_channel, channel_rows)

    if shared_rows:
        siblings = list(
            session.execute(
                select(Channel).where(
                    Channel.user_id == scanning_channel.user_id, Channel.is_active.is_(True)
                )
            ).scalars()
        )
        for sibling in siblings:
            relevance_service.recompute_for_channel(session, sibling, shared_rows)


def freshness_of(row: TrendingTopic, *, now: datetime | None = None) -> dict[str, Any]:
    """Freshness derived from real timestamps, with an explicit state label."""
    now = now or datetime.now(UTC)
    reference = row.published_at or row.discovered_at
    if reference is None:
        return {"state": "UNKNOWN", "age_seconds": None, "basis": None}
    age = (now - reference).total_seconds()
    basis = "published_at" if row.published_at else "discovered_at"
    state = "FRESH" if age <= FRESHNESS_WINDOW.total_seconds() else "STALE"
    return {"state": state, "age_seconds": int(age), "basis": basis}
