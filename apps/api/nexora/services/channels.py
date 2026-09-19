"""Channel creation and settings.

Initial defaults required by the product spec: channel *NEXORA Global*, English
primary / Bangla secondary, autopilot OFF, auto-publishing OFF, human approval
REQUIRED, one video per day, long-form, 5–10 minutes.
"""

from __future__ import annotations

import re
import uuid
import zoneinfo
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, NotFound, PermissionDenied, ValidationError
from nexora.db.models import AutomationSettings, Channel, ChannelSettings, User, YouTubeConnection
from nexora.db.models.enums import AutomationMode, ConnectionStatus, VideoFormat

DEFAULT_CHANNEL_NAME = "NEXORA Global"
DEFAULT_PRIMARY_LANGUAGE = "en"
DEFAULT_SECONDARY_LANGUAGE = "bn"
DEFAULT_TIMEZONE = "Asia/Dhaka"
DEFAULT_CATEGORIES = ["business", "technology", "ai", "future"]

SUPPORTED_CATEGORIES = [
    "business",
    "technology",
    "future",
    "ai",
    "science",
    "digital_economy",
    "global_developments",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug[:160] or "channel"


def validate_timezone(name: str) -> str:
    try:
        zoneinfo.ZoneInfo(name)
    except Exception as exc:
        raise ValidationError(f"'{name}' is not a recognised IANA timezone.") from exc
    return name


def validate_categories(categories: list[str] | None) -> list[str]:
    if categories is None:
        return list(DEFAULT_CATEGORIES)
    cleaned = []
    for item in categories:
        key = str(item).strip().lower().replace(" ", "_").replace("-", "_")
        if key not in SUPPORTED_CATEGORIES:
            raise ValidationError(
                f"Unsupported category '{item}'. Supported: {', '.join(SUPPORTED_CATEGORIES)}."
            )
        if key not in cleaned:
            cleaned.append(key)
    if not cleaned:
        raise ValidationError("Select at least one content category.")
    return cleaned


def create_channel(
    session: Session,
    user: User,
    *,
    name: str = DEFAULT_CHANNEL_NAME,
    description: str | None = None,
    primary_language: str = DEFAULT_PRIMARY_LANGUAGE,
    secondary_language: str | None = DEFAULT_SECONDARY_LANGUAGE,
    timezone: str = DEFAULT_TIMEZONE,
    categories: list[str] | None = None,
) -> Channel:
    channel_name = (name or "").strip()
    if not channel_name:
        raise ValidationError("Channel name is required.")
    slug = slugify(channel_name)
    existing = session.execute(
        select(Channel).where(Channel.user_id == user.id, Channel.slug == slug)
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"You already have a channel named '{channel_name}'.")

    channel = Channel(
        user_id=user.id,
        name=channel_name[:160],
        slug=slug,
        description=description,
        primary_language=primary_language,
        secondary_language=secondary_language,
        timezone=validate_timezone(timezone),
        categories=validate_categories(categories),
        branding={},
    )
    session.add(channel)
    session.flush()

    session.add(
        ChannelSettings(
            channel_id=channel.id,
            default_video_format=VideoFormat.LONG_FORM.value,
            target_duration_min_seconds=300,
            target_duration_max_seconds=600,
        )
    )
    # Conservative by construction: autopilot OFF, auto-publish OFF, approval REQUIRED.
    session.add(
        AutomationSettings(
            channel_id=channel.id,
            mode=AutomationMode.ASSISTED.value,
            autopilot_enabled=False,
            auto_publish_enabled=False,
            require_human_approval=True,
            max_videos_per_day=1,
            timezone=channel.timezone,
        )
    )
    session.add(
        YouTubeConnection(channel_id=channel.id, status=ConnectionStatus.NOT_CONNECTED.value, scopes=[])
    )
    session.flush()
    return channel


def get_channel_for_user(session: Session, user: User, channel_id: uuid.UUID) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFound("Channel not found.")
    if channel.user_id != user.id:
        raise PermissionDenied("You do not have access to that channel.")
    return channel


def list_channels(session: Session, user: User) -> list[Channel]:
    return list(
        session.execute(
            select(Channel).where(Channel.user_id == user.id).order_by(Channel.created_at.asc())
        ).scalars()
    )


def default_channel(session: Session, user: User) -> Channel | None:
    channels = list_channels(session, user)
    return channels[0] if channels else None


def get_automation_settings(session: Session, channel_id: uuid.UUID) -> AutomationSettings:
    record = session.execute(
        select(AutomationSettings).where(AutomationSettings.channel_id == channel_id)
    ).scalar_one_or_none()
    if record is None:
        record = AutomationSettings(channel_id=channel_id)
        session.add(record)
        session.flush()
    return record


def get_channel_settings(session: Session, channel_id: uuid.UUID) -> ChannelSettings:
    record = session.execute(
        select(ChannelSettings).where(ChannelSettings.channel_id == channel_id)
    ).scalar_one_or_none()
    if record is None:
        record = ChannelSettings(channel_id=channel_id)
        session.add(record)
        session.flush()
    return record


def channel_to_dict(channel: Channel) -> dict[str, Any]:
    return {
        "id": str(channel.id),
        "name": channel.name,
        "slug": channel.slug,
        "description": channel.description,
        "primary_language": channel.primary_language,
        "secondary_language": channel.secondary_language,
        "timezone": channel.timezone,
        "categories": channel.categories,
        "is_active": channel.is_active,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
    }
