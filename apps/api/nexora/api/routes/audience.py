"""Channel profile, content-category vocabulary and per-channel trend relevance."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.core.errors import NotFound
from nexora.db.models import ChannelTopicRelevance, TrendingTopic
from nexora.db.models.enums import AudienceClassification, RelevanceStatus
from nexora.services import audit
from nexora.services import categories as category_service
from nexora.services import channels as channel_service
from nexora.services import profiles as profile_service
from nexora.services.trends import relevance as relevance_service

router = APIRouter(prefix="/api/channels", tags=["audience"])
catalog_router = APIRouter(prefix="/api/categories", tags=["audience"])


class ProfileRequest(ApiModel):
    audience_description: str | None = Field(default=None, max_length=2000)
    secondary_categories: list[str] | None = None
    #: Explicit only. Sending nothing leaves it as-is; sending null clears it back to
    #: undeclared. It is never derived from the channel's name or categories.
    audience_classification: str | None = None
    country_region: str | None = Field(default=None, max_length=16)
    secondary_languages: list[str] | None = None
    translation_enabled: bool | None = None
    short_form_enabled: bool | None = None
    long_form_enabled: bool | None = None
    preferred_duration_seconds: int | None = Field(default=None, ge=15, le=7200)
    target_videos_per_week: int | None = Field(default=None, ge=0, le=168)
    brand_voice: str | None = Field(default=None, max_length=2000)
    preferred_topics: list[str] | None = None
    blocked_topics: list[str] | None = None
    content_exclusions: list[str] | None = None
    sensitive_content_restrictions: list[str] | None = None


class CategoryRequest(ApiModel):
    key: str = Field(max_length=64)
    label: str = Field(max_length=120)
    keywords: list[str]
    description: str | None = Field(default=None, max_length=1000)
    audience_hint: str | None = Field(default=None, max_length=32)


class CategoryUpdateRequest(ApiModel):
    label: str | None = Field(default=None, max_length=120)
    keywords: list[str] | None = None
    description: str | None = Field(default=None, max_length=1000)
    audience_hint: str | None = Field(default=None, max_length=32)


# ---------------------------------------------------------------------- profile
@router.get("/{channel_id}/profile")
def get_profile(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    profile = profile_service.get_profile(db, channel.id)
    return profile_service.profile_to_dict(channel, profile)


@router.patch("/{channel_id}/profile")
def update_profile(
    channel_id: uuid.UUID,
    payload: ProfileRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    before = profile_service.profile_to_dict(channel, profile_service.get_profile(db, channel.id))

    profile = profile_service.update_profile(
        db, channel, payload.model_dump(exclude_unset=True)
    )
    after = profile_service.profile_to_dict(channel, profile)

    audit.record(
        db,
        action="channel.profile_updated",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel_profile",
        entity_id=profile.id,
        before=before,
        after=after,
        **ctx,
    )
    return after


@router.get("/{channel_id}/profile/options")
def profile_options(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    """What a person may choose from, read from the database rather than hard-coded."""
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    return {
        "categories": [
            category_service.category_to_dict(row)
            for row in category_service.list_categories(db, channel.id)
        ],
        "audience_classifications": [
            {"value": item.value, "label": item.value.replace("_", " ").title()}
            for item in AudienceClassification
        ],
        "audience_note": (
            "Audience classification is NEXORA's editorial notion of who a channel is "
            "for. It is separate from YouTube's made-for-kids declaration, and neither "
            "is ever derived from the other."
        ),
    }


# ------------------------------------------------------------------- categories
@catalog_router.get("")
def list_categories(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """The content vocabulary, optionally including one channel's own additions."""
    resolved: uuid.UUID | None = None
    if channel_id is not None:
        resolved = channel_service.get_channel_for_user(db, current.user, channel_id).id
    rows = category_service.list_categories(db, resolved)
    return {
        "items": [category_service.category_to_dict(row) for row in rows],
        "total": len(rows),
        "note": (
            "This vocabulary is stored data, not a fixed list. A channel can add its "
            "own categories, and keywords are what the relevance engine matches on."
        ),
    }


@catalog_router.post("", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID,
) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    row = category_service.create_category(
        db,
        channel_id=channel.id,
        key=payload.key,
        label=payload.label,
        keywords=payload.keywords,
        description=payload.description,
        audience_hint=payload.audience_hint,
    )
    audit.record(
        db,
        action="category.created",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="content_category",
        entity_id=row.id,
        after=category_service.category_to_dict(row),
        **ctx,
    )
    return category_service.category_to_dict(row)


@catalog_router.patch("/{category_id}")
def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID,
) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    row = category_service.update_category(
        db, channel.id, category_id, **payload.model_dump(exclude_unset=True)
    )
    return category_service.category_to_dict(row)


@catalog_router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: uuid.UUID, db: DbSession, current: Writer, channel_id: uuid.UUID
) -> None:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    category_service.delete_category(db, channel.id, category_id)


# -------------------------------------------------------------------- relevance
@router.get("/{channel_id}/relevance/{topic_id}")
def explain_relevance(
    channel_id: uuid.UUID, topic_id: uuid.UUID, db: DbSession, current: AuthUser
) -> dict[str, Any]:
    """Why this channel was, or was not, shown this trend.

    Every field here is a stored fact: a keyword that matched, a rule the operator
    configured, a count that was measured. Nothing is a model's opinion.
    """
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    row = db.get(TrendingTopic, topic_id)
    if row is None or not (
        row.channel_id == channel.id
        or (row.channel_id is None and row.user_id == current.user.id)
    ):
        raise NotFound("Trend item not found for this channel.")

    record = db.execute(
        select(ChannelTopicRelevance).where(
            ChannelTopicRelevance.channel_id == channel.id,
            ChannelTopicRelevance.trending_topic_id == topic_id,
        )
    ).scalar_one_or_none()

    profile = profile_service.get_profile(db, channel.id)
    payload: dict[str, Any] = {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "trending_topic_id": str(row.id),
        "title": row.title,
        "signal_score": row.signal_score,
        "matching_inputs": profile_service.matching_inputs(channel, profile),
    }
    if record is None:
        payload["relevance"] = {
            "status": RelevanceStatus.INSUFFICIENT_DATA.value,
            "relevance_reasons": [
                {
                    "code": "not_yet_evaluated",
                    "detail": (
                        "This item has not been evaluated for this channel yet. Run a "
                        "trend scan or re-rank to compute it."
                    ),
                }
            ],
        }
        return payload

    payload["relevance"] = relevance_service.relevance_to_dict(record)
    return payload


@router.post("/{channel_id}/relevance/recompute")
def recompute_relevance(
    channel_id: uuid.UUID,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict[str, Any]:
    """Re-rank this channel's visible trends after a profile or category change.

    Needed because relevance is stored, not computed on read: editing a profile must
    visibly change the ranking rather than leaving stale verdicts in place.
    """
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    rows = list(
        db.execute(
            select(TrendingTopic)
            .where(
                (TrendingTopic.channel_id == channel.id)
                | (
                    TrendingTopic.channel_id.is_(None)
                    & (TrendingTopic.user_id == current.user.id)
                )
            )
            .order_by(TrendingTopic.discovered_at.desc())
            .limit(limit)
        ).scalars()
    )
    records = relevance_service.recompute_for_channel(db, channel, rows)

    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1

    audit.record(
        db,
        action="channel.relevance_recomputed",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel",
        entity_id=channel.id,
        after={"evaluated": len(records), "by_status": counts},
        **ctx,
    )
    return {"evaluated": len(records), "by_status": counts, "channel_id": str(channel.id)}
