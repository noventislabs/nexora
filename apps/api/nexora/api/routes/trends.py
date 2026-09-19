"""Trend discovery and topic candidate endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import func, select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.core.errors import Conflict, NotFound, ValidationError
from nexora.db.models import TopicCandidate, TrendingTopic
from nexora.db.models.enums import ActorType, CandidateStatus, TrendSourceKind
from nexora.queue import jobs as job_queue
from nexora.queue.types import TOPIC_GENERATION, TREND_SCAN
from nexora.services import audit
from nexora.services import channels as channel_service
from nexora.services.availability import (
    reddit_availability,
    youtube_data_api_availability,
)
from nexora.services.trends import sources as source_service
from nexora.services.trends.scan import freshness_of, scan_channel
from nexora.services.trends.scoring import WEIGHTS

router = APIRouter(prefix="/api/trends", tags=["trends"])
topics_router = APIRouter(prefix="/api/topics", tags=["topics"])


# --------------------------------------------------------------------------- schemas
class CreateSourceRequest(ApiModel):
    kind: Literal["youtube_data_api", "rss", "reddit"]
    name: str = Field(max_length=160)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    reliability: float | None = Field(default=None, ge=0.0, le=1.0)
    region: str | None = Field(default=None, max_length=16)
    min_interval_minutes: int = Field(default=60, ge=5, le=10080)


class UpdateSourceRequest(ApiModel):
    name: str | None = Field(default=None, max_length=160)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    reliability: float | None = Field(default=None, ge=0.0, le=1.0)
    region: str | None = Field(default=None, max_length=16)
    min_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class ScanRequest(ApiModel):
    source_ids: list[uuid.UUID] | None = None
    force: bool = False
    limit_per_source: int = Field(default=50, ge=1, le=100)
    background: bool = True


class GenerateTopicsRequest(ApiModel):
    count: int = Field(default=6, ge=1, le=12)
    min_score: int | None = Field(default=None, ge=0, le=100)
    trend_ids: list[uuid.UUID] | None = None
    background: bool = False


class TopicDecisionRequest(ApiModel):
    status: Literal["approved", "rejected", "saved"]
    note: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------------ serializers
def _trend_dict(row: TrendingTopic) -> dict[str, Any]:
    """Serialize a trend observation. Absent facts stay null — never zero."""
    return {
        "id": str(row.id),
        "title": row.title,
        "summary": row.summary,
        "url": row.url,
        "source": {
            "id": str(row.source_id),
            "name": row.source_name,
            "kind": row.source_kind,
        },
        "category": row.category,
        "language": row.language,
        "author": row.author,
        "region": row.region,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
        "freshness": freshness_of(row),
        # Only metrics the upstream actually returned.
        "engagement": row.engagement or {},
        "corroboration_count": row.corroboration_count,
        "duplicate_of_id": str(row.duplicate_of_id) if row.duplicate_of_id else None,
        "opportunity_score": row.opportunity_score,
        "score_breakdown": row.score_breakdown,
        "scored_at": row.scored_at.isoformat() if row.scored_at else None,
    }


def _candidate_dict(row: TopicCandidate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "angle": row.angle,
        "audience": row.audience,
        "category": row.category,
        "why_now": row.why_now,
        "risks": row.risks or [],
        "sources": row.sources or [],
        "opportunity_score": row.opportunity_score,
        "score_breakdown": row.score_breakdown,
        "competition_level": row.competition_level,
        "evidence_count": row.evidence_count,
        "evidence_source_kinds": row.evidence_source_kinds or [],
        "newest_evidence_at": row.newest_evidence_at.isoformat() if row.newest_evidence_at else None,
        "oldest_evidence_at": row.oldest_evidence_at.isoformat() if row.oldest_evidence_at else None,
        "status": row.status,
        "generated_by": {"provider": row.generated_by_provider, "model": row.generated_by_model},
        "generation_run_id": str(row.generation_run_id) if row.generation_run_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
    }


def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


# ----------------------------------------------------------------------------- trends
@router.get("")
def list_trends(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    source_kind: Annotated[str | None, Query(max_length=48)] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    include_duplicates: bool = False,
    scored_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)

    conditions = [TrendingTopic.channel_id == channel.id]
    if not include_duplicates:
        conditions.append(TrendingTopic.duplicate_of_id.is_(None))
    if category:
        conditions.append(TrendingTopic.category == category)
    if source_kind:
        conditions.append(TrendingTopic.source_kind == source_kind)
    if min_score is not None:
        conditions.append(TrendingTopic.opportunity_score >= min_score)
    if scored_only:
        conditions.append(TrendingTopic.opportunity_score.is_not(None))

    total = db.execute(select(func.count()).select_from(TrendingTopic).where(*conditions)).scalar_one()
    rows = list(
        db.execute(
            select(TrendingTopic)
            .where(*conditions)
            .order_by(
                TrendingTopic.opportunity_score.desc().nullslast(),
                TrendingTopic.discovered_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [_trend_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "channel_id": str(channel.id),
    }


@router.get("/scoring-model")
def scoring_model(current: AuthUser) -> dict[str, Any]:
    """The exact Opportunity Score definition, so the number is never a black box."""
    from nexora.services.trends.scoring import MIN_AVAILABLE_WEIGHT, VELOCITY_REFERENCE

    return {
        "name": "Opportunity Score",
        "range": [0, 100],
        "not_a_prediction": (
            "This is not a probability of views, virality or revenue. It ranks how "
            "workable a topic looks given the evidence that was actually collected."
        ),
        "formula": (
            "score = Σ(component_value × weight) / Σ(weight), over only the components "
            "whose inputs were present. A component with a missing input is dropped "
            "rather than defaulted to a value."
        ),
        "minimum_available_weight": MIN_AVAILABLE_WEIGHT,
        "unavailable_rule": (
            f"If computable components carry less than {MIN_AVAILABLE_WEIGHT:.0%} of the "
            "total weight, the score is reported as unavailable instead of being guessed."
        ),
        "weights": WEIGHTS,
        "velocity_reference_per_hour": VELOCITY_REFERENCE,
        "components": {
            "trend_velocity": "Engagement divided by age, log-scaled against a per-source reference. Needs an engagement figure and a publication time.",
            "audience_relevance": "Keyword overlap between the item text and the channel's configured categories.",
            "competition": "Share of other items in the same scan covering overlapping ground. Measures the configured sources only, not all of YouTube.",
            "content_availability": "Corroborating items and how many distinct source kinds carry the story.",
            "recency": "Age since publication, with a 72-hour half-life.",
            "evergreen_value": "Whether the phrasing is explainer-shaped or tied to a moment.",
            "source_reliability": "The operator-configured reliability weight of the source.",
        },
    }


@router.get("/sources")
def list_sources(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    sources = source_service.list_sources(db, channel.id)
    return {
        "items": [source_service.source_to_dict(source) for source in sources],
        "total": len(sources),
        # Deployment-level provider status, distinct from any individual source's health.
        "providers": {
            "rss": {
                "status": "AVAILABLE",
                "provider": "rss",
                "detail": "RSS needs no credentials; each source is validated by its feed URL.",
                "missing_settings": [],
                "metadata": {},
            },
            "youtube_data_api": youtube_data_api_availability().to_dict(),
            "reddit": reddit_availability().to_dict(),
        },
        "supported_kinds": [member.value for member in TrendSourceKind],
    }


@router.post("/sources", status_code=status.HTTP_201_CREATED)
def create_source(
    payload: CreateSourceRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind=payload.kind,
        name=payload.name,
        config=payload.config,
        enabled=payload.enabled,
        reliability=payload.reliability,
        region=payload.region,
        min_interval_minutes=payload.min_interval_minutes,
    )
    audit.record(
        db,
        action="trends.source_created",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="trend_source",
        entity_id=source.id,
        summary=f"Created {source.kind} source '{source.name}'",
        after=source_service.source_to_dict(source),
        **ctx,
    )
    return source_service.source_to_dict(source)


@router.post("/sources/seed-defaults", status_code=status.HTTP_201_CREATED)
def seed_defaults(
    db: DbSession, current: Writer, ctx: RequestContext, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Add the starter RSS feeds so a new channel has a working source immediately."""
    channel = _resolve_channel(db, current, channel_id)
    created = source_service.seed_default_sources(db, channel.id)
    audit.record(
        db,
        action="trends.default_sources_seeded",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel",
        entity_id=channel.id,
        summary=f"Seeded {len(created)} default RSS source(s).",
        **ctx,
    )
    return {
        "items": [source_service.source_to_dict(source) for source in created],
        "created": len(created),
    }


@router.patch("/sources/{source_id}")
def update_source(
    source_id: uuid.UUID,
    payload: UpdateSourceRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    source = source_service.get_source(db, channel.id, source_id)
    before = source_service.source_to_dict(source)

    updates = payload.model_dump(exclude_unset=True)
    if "config" in updates and updates["config"] is not None:
        source.config = source_service.validate_config(source.kind, updates["config"])
    if updates.get("name"):
        name = updates["name"].strip()
        if not name:
            raise ValidationError("Source name is required.")
        source.name = name[:160]
    if updates.get("enabled") is not None:
        source.enabled = updates["enabled"]
    if updates.get("reliability") is not None:
        source.reliability = updates["reliability"]
    if "region" in updates:
        source.region = updates["region"]
    if updates.get("min_interval_minutes") is not None:
        source.min_interval_minutes = updates["min_interval_minutes"]
    db.flush()

    audit.record(
        db,
        action="trends.source_updated",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="trend_source",
        entity_id=source.id,
        before=before,
        after=source_service.source_to_dict(source),
        **ctx,
    )
    return source_service.source_to_dict(source)


@router.post("/scan")
def scan(
    payload: ScanRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Run a scan now, or queue it for the worker."""
    channel = _resolve_channel(db, current, channel_id)
    if not source_service.list_sources(db, channel.id):
        raise NotFound(
            "This channel has no trend sources. Add one, or seed the default RSS feeds."
        )

    if payload.background:
        job = job_queue.enqueue(
            db,
            TREND_SCAN,
            channel_id=channel.id,
            payload={
                "source_ids": [str(value) for value in payload.source_ids or []],
                "force": payload.force,
                "limit_per_source": payload.limit_per_source,
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        audit.record(
            db,
            action="trends.scan_queued",
            user_id=current.user.id,
            channel_id=channel.id,
            entity_type="job",
            entity_id=job.id,
            **ctx,
        )
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = scan_channel(
        db,
        channel,
        source_ids=payload.source_ids,
        force=payload.force,
        limit_per_source=payload.limit_per_source,
        actor_type=ActorType.USER,
        user_id=current.user.id,
    )
    return {"mode": "inline", **result.to_dict()}


@router.get("/{trend_id}")
def get_trend(
    trend_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    row = db.get(TrendingTopic, trend_id)
    if row is None or row.channel_id != channel.id:
        raise NotFound("Trend item not found.")
    duplicates = list(
        db.execute(
            select(TrendingTopic).where(TrendingTopic.duplicate_of_id == row.id).limit(25)
        ).scalars()
    )
    return {
        **_trend_dict(row),
        "corroborating_items": [
            {
                "id": str(item.id),
                "title": item.title,
                "source_name": item.source_name,
                "source_kind": item.source_kind,
                "url": item.url,
                "discovered_at": item.discovered_at.isoformat() if item.discovered_at else None,
            }
            for item in duplicates
        ],
    }


# ----------------------------------------------------------------------------- topics
@topics_router.get("")
def list_topics(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    conditions = [TopicCandidate.channel_id == channel.id]
    if status_filter:
        conditions.append(TopicCandidate.status == status_filter)

    total = db.execute(select(func.count()).select_from(TopicCandidate).where(*conditions)).scalar_one()
    rows = list(
        db.execute(
            select(TopicCandidate)
            .where(*conditions)
            .order_by(
                TopicCandidate.opportunity_score.desc().nullslast(),
                TopicCandidate.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [_candidate_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "channel_id": str(channel.id),
    }


@topics_router.post("/generate")
def generate_topics(
    payload: GenerateTopicsRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Generate topic candidates from collected trend evidence.

    Returns HTTP 503 ``provider_not_configured`` when no LLM provider is set up. It
    never substitutes locally invented topics.
    """
    from nexora.services.topics import generate_candidates

    channel = _resolve_channel(db, current, channel_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            TOPIC_GENERATION,
            channel_id=channel.id,
            payload={
                "count": payload.count,
                "min_score": payload.min_score,
                "trend_ids": [str(value) for value in payload.trend_ids or []],
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = generate_candidates(
        db,
        channel,
        count=payload.count,
        min_score=payload.min_score,
        trend_ids=payload.trend_ids,
        actor_type=ActorType.USER,
        user_id=current.user.id,
    )
    return {
        "mode": "inline",
        **result.to_dict(),
        "items": [_candidate_dict(candidate) for candidate in result.candidates],
    }


@topics_router.get("/{topic_id}")
def get_topic(
    topic_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    row = db.get(TopicCandidate, topic_id)
    if row is None or row.channel_id != channel.id:
        raise NotFound("Topic candidate not found.")

    evidence = []
    for reference in row.sources or []:
        trend_id = reference.get("trending_topic_id")
        item = db.get(TrendingTopic, uuid.UUID(trend_id)) if trend_id else None
        evidence.append(_trend_dict(item) if item is not None else {**reference, "deleted": True})
    return {**_candidate_dict(row), "evidence": evidence}


@topics_router.post("/{topic_id}/decision")
def decide_topic(
    topic_id: uuid.UUID,
    payload: TopicDecisionRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    from nexora.services.topics import decide

    channel = _resolve_channel(db, current, channel_id)
    row = db.get(TopicCandidate, topic_id)
    if row is None or row.channel_id != channel.id:
        raise NotFound("Topic candidate not found.")
    if row.status == CandidateStatus.CONVERTED.value:
        raise Conflict("This candidate has already been turned into a content project.")

    decide(db, row, status=CandidateStatus(payload.status), user_id=current.user.id, note=payload.note)
    return _candidate_dict(row)
