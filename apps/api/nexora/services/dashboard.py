"""Dashboard overview.

Rule enforced here: a number is only returned when it was actually counted. Anything
the system cannot know yet is returned as ``None`` together with an explicit
``unavailable_reason``, so the UI renders ``—`` or ``NOT CONNECTED`` rather than ``0``.
"""

from __future__ import annotations

import uuid
import zoneinfo
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexora.db.models import (
    AnalyticsSnapshot,
    Channel,
    ContentProject,
    Job,
    PublishJob,
    TopicCandidate,
    TrendingTopic,
    VideoRenderJob,
    YouTubeConnection,
)
from nexora.db.models.enums import (
    AnalyticsScope,
    CandidateStatus,
    ConnectionStatus,
    ProjectStatus,
    RunStatus,
)
from nexora.services.availability import youtube_connection_availability


def _local_day_bounds(timezone: str) -> tuple[datetime, datetime]:
    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except Exception:
        tz = UTC
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC), (start_local + timedelta(days=1)).astimezone(UTC)


def _unavailable(reason: str) -> dict[str, Any]:
    return {"value": None, "available": False, "unavailable_reason": reason}


def _value(value: int) -> dict[str, Any]:
    return {"value": value, "available": True, "unavailable_reason": None}


def today_counters(session: Session, channel: Channel) -> dict[str, Any]:
    """Counts of work this system performed today, in the channel's local day."""
    start, end = _local_day_bounds(channel.timezone)

    def count(stmt) -> int:
        return session.execute(stmt).scalar_one()

    trending = count(
        select(func.count())
        .select_from(TrendingTopic)
        .where(
            TrendingTopic.channel_id == channel.id,
            TrendingTopic.collected_at >= start,
            TrendingTopic.collected_at < end,
        )
    )
    ideas = count(
        select(func.count())
        .select_from(TopicCandidate)
        .where(
            TopicCandidate.channel_id == channel.id,
            TopicCandidate.created_at >= start,
            TopicCandidate.created_at < end,
        )
    )
    scripts_ready = count(
        select(func.count())
        .select_from(ContentProject)
        .where(
            ContentProject.channel_id == channel.id,
            ContentProject.status.in_(
                [ProjectStatus.FACT_CHECK.value, ProjectStatus.VOICE.value, ProjectStatus.ASSETS.value]
            ),
        )
    )
    rendering = count(
        select(func.count())
        .select_from(VideoRenderJob)
        .join(ContentProject, ContentProject.id == VideoRenderJob.video_project_id, isouter=True)
        .where(VideoRenderJob.status == RunStatus.RUNNING.value)
    )
    scheduled = count(
        select(func.count())
        .select_from(PublishJob)
        .where(
            PublishJob.channel_id == channel.id,
            PublishJob.status == RunStatus.QUEUED.value,
        )
    )
    published = count(
        select(func.count())
        .select_from(PublishJob)
        .where(
            PublishJob.channel_id == channel.id,
            PublishJob.status == RunStatus.SUCCESS.value,
            PublishJob.finished_at >= start,
            PublishJob.finished_at < end,
        )
    )
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "timezone": channel.timezone,
        "trending_topics": _value(trending),
        "ideas_generated": _value(ideas),
        "scripts_ready": _value(scripts_ready),
        "videos_rendering": _value(rendering),
        "scheduled": _value(scheduled),
        "published": _value(published),
    }


def channel_summary(session: Session, channel: Channel) -> dict[str, Any]:
    """Subscriber/view/revenue state, honestly reported.

    Nothing here is estimated. If YouTube is not connected, or the connected scopes do
    not cover a metric, the metric is reported as unavailable with the reason.
    """
    availability = youtube_connection_availability(session, channel.id)
    connection = session.execute(
        select(YouTubeConnection).where(YouTubeConnection.channel_id == channel.id)
    ).scalar_one_or_none()

    if connection is None or connection.status != ConnectionStatus.CONNECTED.value:
        reason = "YouTube API not connected"
        return {
            "name": channel.name,
            "youtube": availability.to_dict(),
            "subscribers": _unavailable(reason),
            "views": _unavailable(reason),
            "videos": _unavailable(reason),
            "revenue": _unavailable(
                "Connect supported monetization/analytics data to display revenue."
            ),
            "last_updated": None,
            "data_source": None,
            "data_age_seconds": None,
        }

    snapshot = session.execute(
        select(AnalyticsSnapshot)
        .where(
            AnalyticsSnapshot.channel_id == channel.id,
            AnalyticsSnapshot.scope == AnalyticsScope.CHANNEL.value,
        )
        .order_by(AnalyticsSnapshot.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if snapshot is None:
        reason = "No analytics snapshot has been collected yet. Run an analytics sync."
        return {
            "name": channel.name,
            "youtube": availability.to_dict(),
            "subscribers": _unavailable(reason),
            "views": _unavailable(reason),
            "videos": _unavailable(reason),
            "revenue": _unavailable(
                "Connect supported monetization/analytics data to display revenue."
            ),
            "last_updated": None,
            "data_source": None,
            "data_age_seconds": None,
        }

    metrics = snapshot.metrics or {}

    def metric(key: str, reason: str) -> dict[str, Any]:
        if key in metrics and metrics[key] is not None:
            return _value(metrics[key])
        return _unavailable(reason)

    age = (datetime.now(UTC) - snapshot.captured_at).total_seconds() if snapshot.captured_at else None
    return {
        "name": channel.name,
        "youtube": availability.to_dict(),
        "subscribers": metric("subscriber_count", "Subscriber count is hidden or not returned by the API"),
        "views": metric("view_count", "View count not returned by the API"),
        "videos": metric("video_count", "Video count not returned by the API"),
        "revenue": (
            metric("estimated_revenue", "Monetary scope not granted by the connected account")
            if connection.has_monetary_scope
            else _unavailable(
                "Connect supported monetization/analytics data to display revenue."
            )
        ),
        "last_updated": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "data_source": snapshot.source,
        "data_age_seconds": int(age) if age is not None else None,
    }


def pipeline_counts(session: Session, channel_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(ContentProject.status, func.count())
        .where(ContentProject.channel_id == channel_id)
        .group_by(ContentProject.status)
    ).all()
    counts = {status.value: 0 for status in ProjectStatus}
    for status_value, count in rows:
        counts[status_value] = count
    return counts


def queue_counts(session: Session, channel_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(Job.status, func.count()).where(Job.channel_id == channel_id).group_by(Job.status)
    ).all()
    counts = {status.value: 0 for status in RunStatus}
    for status_value, count in rows:
        counts[status_value] = count
    return counts


def pending_approvals(session: Session, channel_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(ContentProject)
        .where(
            ContentProject.channel_id == channel_id,
            ContentProject.status == ProjectStatus.READY.value,
            ContentProject.approval_status == "pending",
        )
    ).scalar_one()


def open_ideas(session: Session, channel_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(TopicCandidate)
        .where(
            TopicCandidate.channel_id == channel_id,
            TopicCandidate.status == CandidateStatus.PROPOSED.value,
        )
    ).scalar_one()


def overview(session: Session, channel: Channel) -> dict[str, Any]:
    from nexora.services.channels import get_automation_settings

    automation = get_automation_settings(session, channel.id)
    return {
        "channel": {
            "id": str(channel.id),
            "name": channel.name,
            "timezone": channel.timezone,
            "categories": channel.categories,
        },
        "autopilot": {
            "enabled": automation.autopilot_enabled,
            "mode": automation.mode,
            "auto_publish_enabled": automation.auto_publish_enabled,
            "require_human_approval": automation.require_human_approval,
            "emergency_stop": automation.emergency_stop,
            "emergency_stop_reason": automation.emergency_stop_reason,
            "max_videos_per_day": automation.max_videos_per_day,
        },
        "today": today_counters(session, channel),
        "channel_summary": channel_summary(session, channel),
        "pipeline": pipeline_counts(session, channel.id),
        "queue": queue_counts(session, channel.id),
        "pending_approvals": pending_approvals(session, channel.id),
        "open_ideas": open_ideas(session, channel.id),
    }
