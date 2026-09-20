"""Channel-scoped analytics endpoints.

Every route here is scoped to one channel. There is no cross-channel comparison
endpoint, because a median across channels with different audiences describes nothing
that exists.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.core.errors import NotFound
from nexora.db.models import AnalyticsSnapshot, PerformanceObservation, YouTubeVideo
from nexora.services import analytics as analytics_service
from nexora.services import channels as channel_service
from nexora.services.availability import youtube_oauth_availability

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


@router.get("/overview")
def overview(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """What is actually known about this channel right now.

    With no snapshot collected, this reports that plainly rather than returning zeros.
    """
    from nexora.services.youtube import oauth as oauth_service

    channel = _resolve_channel(db, current, channel_id)
    snapshot = analytics_service.latest_channel_snapshot(db, channel.id)

    try:
        connection = oauth_service.get_connection(db, channel.id)
        capabilities = oauth_service.connection_to_dict(connection)["capabilities"]
    except NotFound:
        capabilities = {}

    return {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "oauth_app": youtube_oauth_availability().to_dict(),
        "capabilities": capabilities,
        "snapshot": analytics_service.snapshot_to_dict(snapshot) if snapshot else None,
        "baselines": {
            metric: analytics_service.channel_baseline(db, channel, metric).to_dict()
            for metric in ("views", "likes", "average_view_percentage")
        },
        "scope_note": (
            "Everything here is measured for this channel alone. NEXORA never compares "
            "one channel against another, because channels with different audiences "
            "have no shared baseline."
        ),
        "no_forecast_note": (
            "These are measurements of videos already published. NEXORA does not "
            "predict views, subscribers or revenue for a future video."
        ),
    }


@router.post("/collect")
def collect(
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
    period_days: Annotated[int, Query(ge=1, le=365)] = 28,
) -> dict[str, Any]:
    """Read this channel's analytics from YouTube and store a snapshot.

    Returns 502/503 when the connection lacks the analytics scope. It does not fall
    back to public data: public reads cannot supply impressions, click-through rate,
    watch time or revenue, and a snapshot missing them would read as a channel with
    none of them.
    """
    channel = _resolve_channel(db, current, channel_id)
    snapshot = analytics_service.collect_channel_snapshot(
        db, channel, period_days=period_days, user_id=current.user.id
    )
    return analytics_service.snapshot_to_dict(snapshot)


@router.post("/collect/video/{video_id}")
def collect_video(
    video_id: uuid.UUID,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
    period_days: Annotated[int, Query(ge=1, le=365)] = 28,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    video = analytics_service.get_video(db, channel.id, video_id)
    snapshot = analytics_service.collect_video_snapshot(
        db, channel, video, period_days=period_days
    )
    return analytics_service.snapshot_to_dict(snapshot)


@router.get("/baseline")
def baseline(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    metric: Annotated[str, Query(max_length=48)] = "views",
    period_days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> dict[str, Any]:
    """This channel's own median for a metric, or why there isn't one."""
    channel = _resolve_channel(db, current, channel_id)
    result = analytics_service.channel_baseline(db, channel, metric, period_days=period_days)
    return {
        "channel_id": str(channel.id),
        "minimum_sample_size": analytics_service.MIN_SAMPLE_SIZE,
        **result.to_dict(),
    }


@router.get("/video/{video_id}")
def video_comparison(
    video_id: uuid.UUID,
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    metric: Annotated[str, Query(max_length=48)] = "views",
) -> dict[str, Any]:
    """One video measured against its own channel's baseline."""
    channel = _resolve_channel(db, current, channel_id)
    video = analytics_service.get_video(db, channel.id, video_id)
    result = analytics_service.compare_video_to_baseline(db, channel, video, metric)

    payload: dict[str, Any] = {
        "video_id": str(video.id),
        "youtube_video_id": video.youtube_video_id,
        "title": video.title,
        "channel_id": str(channel.id),
    }
    if isinstance(result, analytics_service.Comparison):
        payload["comparison"] = result.to_dict()
    else:
        payload["comparison"] = None
        payload["unavailable"] = result.to_dict()
    return payload


@router.post("/video/{video_id}/observe")
def record_observation(
    video_id: uuid.UUID,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
    metric: Annotated[str, Query(max_length=48)] = "views",
) -> dict[str, Any]:
    """Store the current comparison as a durable observation."""
    channel = _resolve_channel(db, current, channel_id)
    video = analytics_service.get_video(db, channel.id, video_id)
    result = analytics_service.compare_video_to_baseline(db, channel, video, metric)
    if not isinstance(result, analytics_service.Comparison):
        return {"recorded": False, "unavailable": result.to_dict()}

    row = analytics_service.record_observation(db, channel, video, result)
    return {
        "recorded": True,
        "id": str(row.id),
        "observation": row.observation,
        "measured": row.measured,
        "possible_factors": row.possible_factors,
    }


@router.get("/observations")
def list_observations(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    rows = list(
        db.execute(
            select(PerformanceObservation)
            .where(PerformanceObservation.channel_id == channel.id)
            .order_by(PerformanceObservation.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "video_id": str(row.video_id),
                "signal": row.signal,
                "observation": row.observation,
                "possible_factors": row.possible_factors or [],
                "suggested_investigation": row.suggested_investigation,
                "measured": row.measured or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": len(rows),
        "note": (
            "Observations describe what was measured. They name possible contributing "
            "factors, never causes, and they do not forecast future performance."
        ),
    }


@router.get("/categories")
def categories(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    period_days: Annotated[int, Query(ge=30, le=730)] = 180,
) -> dict[str, Any]:
    """Median views per topic category, within this channel only."""
    channel = _resolve_channel(db, current, channel_id)
    return analytics_service.category_performance(db, channel, period_days=period_days)


@router.get("/snapshots")
def list_snapshots(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    rows = list(
        db.execute(
            select(AnalyticsSnapshot)
            .where(AnalyticsSnapshot.channel_id == channel.id)
            .order_by(AnalyticsSnapshot.captured_at.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "items": [analytics_service.snapshot_to_dict(row) for row in rows],
        "total": len(rows),
    }


@router.get("/videos")
def list_videos_with_metrics(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    """This channel's videos with whatever metrics have been collected for each."""
    from nexora.db.models import ContentPerformanceFeature

    channel = _resolve_channel(db, current, channel_id)
    rows = db.execute(
        select(YouTubeVideo, ContentPerformanceFeature)
        .outerjoin(
            ContentPerformanceFeature,
            ContentPerformanceFeature.video_id == YouTubeVideo.id,
        )
        .where(YouTubeVideo.channel_id == channel.id)
        .order_by(YouTubeVideo.published_at.desc().nullslast())
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "video_id": str(video.id),
                "youtube_video_id": video.youtube_video_id,
                "title": video.title,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                # Every metric is null when not collected — never 0.
                "metrics": {
                    "views": features.views if features else None,
                    "likes": features.likes if features else None,
                    "comments": features.comments if features else None,
                    "impressions": features.impressions if features else None,
                    "click_through_rate": features.ctr_percent if features else None,
                    "average_view_percentage": (
                        features.average_view_percentage if features else None
                    ),
                },
                "has_metrics": features is not None,
            }
            for video, features in rows
        ],
        "total": len(rows),
    }
