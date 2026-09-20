"""Channel, channel-settings and automation-settings endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, status

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import (
    AutomationSettingsRequest,
    ChannelSettingsRequest,
    CreateChannelRequest,
    EmergencyStopRequest,
    UpdateChannelRequest,
)
from nexora.core.errors import ValidationError
from nexora.db.models.enums import ActorType, AutomationMode
from nexora.services import audit
from nexora.services import channels as channel_service
from nexora.services.availability import youtube_connection_availability

router = APIRouter(prefix="/api/channels", tags=["channels"])


def _settings_dict(record: Any) -> dict[str, Any]:
    return {
        "default_video_format": record.default_video_format,
        "target_duration_min_seconds": record.target_duration_min_seconds,
        "target_duration_max_seconds": record.target_duration_max_seconds,
        "narration_tone": record.narration_tone,
        "call_to_action": record.call_to_action,
        "aspect_ratio": record.aspect_ratio,
        "resolution": record.resolution,
        "subtitle_burn_in": record.subtitle_burn_in,
        "preferred_voice_id": record.preferred_voice_id,
        "editorial_notes": record.editorial_notes,
        "made_for_kids_default": record.made_for_kids_default,
        "youtube_category_id": record.youtube_category_id,
    }


def _automation_dict(record: Any) -> dict[str, Any]:
    return {
        "mode": record.mode,
        "automation_enabled": record.automation_enabled,
        "publishing_enabled": record.publishing_enabled,
        "allow_unverified_commentary": record.allow_unverified_commentary,
        "autopilot_enabled": record.autopilot_enabled,
        "auto_publish_enabled": record.auto_publish_enabled,
        "require_human_approval": record.require_human_approval,
        "max_videos_per_day": record.max_videos_per_day,
        "max_videos_per_week": record.max_videos_per_week,
        "min_interval_minutes": record.min_interval_minutes,
        "publish_window_start_hour": record.publish_window_start_hour,
        "publish_window_end_hour": record.publish_window_end_hour,
        "preferred_publish_hour": record.preferred_publish_hour,
        "timezone": record.timezone,
        "min_quality_score": record.min_quality_score,
        "min_originality_score": record.min_originality_score,
        "max_copyright_risk": record.max_copyright_risk,
        "block_on_unknown_license": record.block_on_unknown_license,
        "require_fact_check_pass": record.require_fact_check_pass,
        "emergency_stop": record.emergency_stop,
        "emergency_stop_at": record.emergency_stop_at.isoformat() if record.emergency_stop_at else None,
        "emergency_stop_reason": record.emergency_stop_reason,
        "daily_scan_enabled": record.daily_scan_enabled,
        "daily_scan_hour": record.daily_scan_hour,
    }


@router.get("")
def list_channels(db: DbSession, current: AuthUser) -> dict[str, Any]:
    channels = channel_service.list_channels(db, current.user)
    return {
        "items": [
            {
                **channel_service.channel_to_dict(channel),
                "youtube": youtube_connection_availability(db, channel.id).to_dict(),
            }
            for channel in channels
        ],
        "total": len(channels),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: CreateChannelRequest, db: DbSession, current: Writer, ctx: RequestContext
) -> dict[str, Any]:
    channel = channel_service.create_channel(
        db,
        current.user,
        name=payload.name,
        description=payload.description,
        primary_language=payload.primary_language,
        secondary_language=payload.secondary_language,
        timezone=payload.timezone,
        categories=payload.categories,
    )
    audit.record(
        db,
        action="channel.created",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel",
        entity_id=channel.id,
        summary=f"Created channel {channel.name}",
        **ctx,
    )
    return channel_service.channel_to_dict(channel)


@router.get("/{channel_id}")
def get_channel(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    return {
        **channel_service.channel_to_dict(channel),
        "settings": _settings_dict(channel_service.get_channel_settings(db, channel.id)),
        "automation": _automation_dict(channel_service.get_automation_settings(db, channel.id)),
        "youtube": youtube_connection_availability(db, channel.id).to_dict(),
    }


@router.patch("/{channel_id}")
def update_channel(
    channel_id: uuid.UUID,
    payload: UpdateChannelRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    before = channel_service.channel_to_dict(channel)

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise ValidationError("Channel name is required.")
        channel.name = name[:160]
        channel.slug = channel_service.slugify(name)
    if payload.description is not None:
        channel.description = payload.description
    if payload.primary_language is not None:
        channel.primary_language = payload.primary_language
    if payload.secondary_language is not None:
        channel.secondary_language = payload.secondary_language or None
    if payload.timezone is not None:
        channel.timezone = channel_service.validate_timezone(payload.timezone)
    if payload.categories is not None:
        channel.categories = channel_service.validate_categories(
            db, payload.categories, channel_id=channel.id
        )
    db.flush()

    audit.record(
        db,
        action="channel.updated",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel",
        entity_id=channel.id,
        before=before,
        after=channel_service.channel_to_dict(channel),
        **ctx,
    )
    return channel_service.channel_to_dict(channel)


@router.get("/{channel_id}/settings")
def get_settings(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    return _settings_dict(channel_service.get_channel_settings(db, channel.id))


@router.patch("/{channel_id}/settings")
def update_settings(
    channel_id: uuid.UUID,
    payload: ChannelSettingsRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    record = channel_service.get_channel_settings(db, channel.id)
    before = _settings_dict(record)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(record, field, value)
    if record.target_duration_min_seconds > record.target_duration_max_seconds:
        raise ValidationError("Minimum target duration cannot exceed the maximum.")
    db.flush()

    audit.record(
        db,
        action="channel.settings_updated",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="channel_settings",
        entity_id=record.id,
        before=before,
        after=_settings_dict(record),
        **ctx,
    )
    return _settings_dict(record)


@router.get("/{channel_id}/automation")
def get_automation(channel_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    return _automation_dict(channel_service.get_automation_settings(db, channel.id))


@router.patch("/{channel_id}/automation")
def update_automation(
    channel_id: uuid.UUID,
    payload: AutomationSettingsRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
) -> dict[str, Any]:
    """Change autopilot configuration.

    Two guardrails are enforced here rather than in the UI:

    * Switching to AUTONOMOUS is an explicit, audited act — it never happens implicitly.
    * Auto-publishing cannot be enabled while human approval is required, so the two
      switches can never contradict each other.
    """
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    record = channel_service.get_automation_settings(db, channel.id)
    before = _automation_dict(record)

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "timezone" in updates:
        updates["timezone"] = channel_service.validate_timezone(updates["timezone"])

    # Validate the *resulting* configuration before touching the record, so a rejected
    # request can never leave a half-applied automation state behind.
    resulting = {**before, **updates}
    _validate_automation(resulting)

    for field, value in updates.items():
        setattr(record, field, value)
    db.flush()

    after = _automation_dict(record)
    changed = {key: after[key] for key in after if before[key] != after[key]}
    audit.record(
        db,
        action="automation.settings_updated",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="automation_settings",
        entity_id=record.id,
        summary=", ".join(f"{k}={v}" for k, v in changed.items()) or "no change",
        before=before,
        after=after,
        **ctx,
    )
    return after


def _validate_automation(resulting: dict[str, Any]) -> None:
    """Reject automation configurations that contradict the product's safety rules."""
    if resulting["publish_window_start_hour"] == resulting["publish_window_end_hour"]:
        raise ValidationError("Publishing window start and end hours must differ.")
    if resulting["emergency_stop"] and (
        resulting["autopilot_enabled"]
        or resulting["auto_publish_enabled"]
        or resulting["automation_enabled"]
    ):
        raise ValidationError(
            "Emergency stop is engaged. Clear it before re-enabling automation, "
            "autopilot or auto-publishing."
        )
    if resulting["auto_publish_enabled"] and resulting["require_human_approval"]:
        raise ValidationError(
            "Auto-publishing cannot be enabled while human approval is required. "
            "Turn off 'require human approval' explicitly to allow unattended publishing."
        )
    if resulting["mode"] == AutomationMode.AUTONOMOUS.value and not resulting["autopilot_enabled"]:
        raise ValidationError(
            "Autonomous mode requires the autopilot switch to be ON. "
            "Enable autopilot in the same request to confirm."
        )
    if resulting["autopilot_enabled"] and not resulting["automation_enabled"]:
        raise ValidationError(
            "Autopilot cannot be ON while automation is OFF for this channel. "
            "Enable automation in the same request to confirm."
        )
    if resulting["auto_publish_enabled"] and not resulting["publishing_enabled"]:
        raise ValidationError(
            "Auto-publishing cannot be ON while publishing is OFF for this channel."
        )


@router.post("/{channel_id}/automation/emergency-stop")
def emergency_stop(
    channel_id: uuid.UUID,
    payload: EmergencyStopRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
) -> dict[str, Any]:
    """Immediately halt automated publishing and cancel queued publish work."""
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import AUTOMATION_ADVANCE, AUTOMATION_TICK, YOUTUBE_UPLOAD

    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    record = channel_service.get_automation_settings(db, channel.id)
    before = _automation_dict(record)

    record.emergency_stop = True
    record.emergency_stop_at = datetime.now(UTC)
    record.emergency_stop_reason = payload.reason
    record.autopilot_enabled = False
    record.auto_publish_enabled = False
    db.flush()

    cancelled = job_queue.cancel_pending_for_channel(
        db, channel.id, types=(YOUTUBE_UPLOAD, AUTOMATION_TICK, AUTOMATION_ADVANCE)
    )
    audit.record(
        db,
        action="automation.emergency_stop",
        actor_type=ActorType.USER,
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="automation_settings",
        entity_id=record.id,
        summary=f"Emergency stop: {payload.reason} (cancelled {cancelled} queued jobs)",
        before=before,
        after=_automation_dict(record),
        **ctx,
    )
    return {**_automation_dict(record), "cancelled_jobs": cancelled}


@router.post("/{channel_id}/automation/clear-emergency-stop")
def clear_emergency_stop(
    channel_id: uuid.UUID, db: DbSession, current: Writer, ctx: RequestContext
) -> dict[str, Any]:
    """Clear the stop. Autopilot stays OFF and must be re-enabled deliberately."""
    channel = channel_service.get_channel_for_user(db, current.user, channel_id)
    record = channel_service.get_automation_settings(db, channel.id)
    before = _automation_dict(record)

    record.emergency_stop = False
    record.emergency_stop_at = None
    record.emergency_stop_reason = None
    db.flush()

    audit.record(
        db,
        action="automation.emergency_stop_cleared",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="automation_settings",
        entity_id=record.id,
        before=before,
        after=_automation_dict(record),
        **ctx,
    )
    return _automation_dict(record)
