"""Background handlers for trend scanning and topic generation."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound
from nexora.db.models import Channel, Job
from nexora.db.models.enums import ActorType
from nexora.queue import jobs as job_queue
from nexora.queue.types import TOPIC_GENERATION, TREND_SCAN, register_handler
from nexora.services.topics import generate_candidates
from nexora.services.trends.scan import scan_channel


def _channel(session: Session, job: Job) -> Channel:
    channel_id = job.channel_id or _uuid(job.payload.get("channel_id"))
    if channel_id is None:
        raise NotFound("This job has no channel to work on.")
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFound(f"Channel {channel_id} no longer exists.")
    return channel


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


@register_handler(TREND_SCAN)
def handle_trend_scan(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    payload = job.payload or {}
    source_ids = [
        parsed
        for parsed in (_uuid(value) for value in payload.get("source_ids", []) or [])
        if parsed is not None
    ]

    job_queue.log(session, job, f"Scanning trend sources for {channel.name}.")
    result = scan_channel(
        session,
        channel,
        source_ids=source_ids or None,
        force=bool(payload.get("force")),
        limit_per_source=int(payload.get("limit_per_source", 50)),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
        user_id=_uuid(payload.get("user_id")),
    )

    for source_result in result.sources:
        if source_result.status in ("FAILED", "NOT_CONFIGURED"):
            job_queue.log(
                session,
                job,
                f"{source_result.source_name}: {source_result.status} — {source_result.error}",
                level="WARNING",
                source_id=source_result.source_id and str(source_result.source_id),
            )
    job_queue.log(
        session,
        job,
        f"Fetched {result.fetched}, stored {result.stored}, {result.duplicates} duplicate(s).",
    )
    return result.to_dict()


@register_handler(TOPIC_GENERATION)
def handle_topic_generation(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    payload = job.payload or {}

    job_queue.log(session, job, f"Generating topic candidates for {channel.name}.")
    # ProviderNotConfigured propagates: the worker marks it a permanent failure, because
    # a missing API key is not a transient fault.
    result = generate_candidates(
        session,
        channel,
        count=int(payload.get("count", 6)),
        min_score=payload.get("min_score"),
        trend_ids=[
            parsed
            for parsed in (_uuid(value) for value in payload.get("trend_ids", []) or [])
            if parsed is not None
        ]
        or None,
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
        user_id=_uuid(payload.get("user_id")),
    )
    for note in result.dropped:
        job_queue.log(session, job, f"Dropped candidate: {note}", level="WARNING")
    job_queue.log(session, job, f"Created {len(result.candidates)} candidate(s).")
    return result.to_dict()
