"""Users, authentication sessions, channels and their settings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import AutomationMode, ConnectionStatus, UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.OWNER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    onboarding_completed_at = utc_column()
    last_login_at = utc_column()

    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")
    channels: Mapped[list[Channel]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class AuthSession(Base):
    """Server-side session record.

    The raw session token is never stored; only an HMAC fingerprint is persisted so a
    database disclosure does not yield usable credentials.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    revoked_at = utc_column()

    user: Mapped[User] = relationship(back_populates="sessions")


class Channel(Base, TimestampMixin):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    primary_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    secondary_language: Mapped[str | None] = mapped_column(String(16), default="bn")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Dhaka")
    categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_channels_user_slug"),)

    owner: Mapped[User] = relationship(back_populates="channels")
    settings: Mapped[ChannelSettings | None] = relationship(
        back_populates="channel", cascade="all, delete-orphan", uselist=False
    )
    automation_settings: Mapped[AutomationSettings | None] = relationship(
        back_populates="channel", cascade="all, delete-orphan", uselist=False
    )
    youtube_connection: Mapped[YouTubeConnection | None] = relationship(
        back_populates="channel", cascade="all, delete-orphan", uselist=False
    )


class ChannelSettings(Base, TimestampMixin):
    """Editorial defaults for a channel (what content looks like)."""

    __tablename__ = "channel_settings"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    default_video_format: Mapped[str] = mapped_column(String(32), nullable=False, default="long_form")
    target_duration_min_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    target_duration_max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    narration_tone: Mapped[str] = mapped_column(String(64), nullable=False, default="informative")
    call_to_action: Mapped[str | None] = mapped_column(Text)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False, default="16:9")
    resolution: Mapped[str] = mapped_column(String(16), nullable=False, default="1080p")
    subtitle_burn_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    preferred_voice_id: Mapped[str | None] = mapped_column(String(128))
    editorial_notes: Mapped[str | None] = mapped_column(Text)

    channel: Mapped[Channel] = relationship(back_populates="settings")


class AutomationSettings(Base, TimestampMixin):
    """Autopilot switches, publishing limits and safety thresholds.

    Defaults are deliberately conservative: autopilot OFF, auto-publish OFF,
    human approval REQUIRED, one video per day.
    """

    __tablename__ = "automation_settings"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    mode: Mapped[str] = mapped_column(String(32), nullable=False, default=AutomationMode.ASSISTED.value)
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    max_videos_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_videos_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    min_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=720)

    publish_window_start_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    publish_window_end_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    preferred_publish_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=19)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Dhaka")

    min_quality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    min_originality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    max_copyright_risk: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    block_on_unknown_license: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_fact_check_pass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stop_at = utc_column()
    emergency_stop_reason: Mapped[str | None] = mapped_column(Text)

    daily_scan_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_scan_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

    channel: Mapped[Channel] = relationship(back_populates="automation_settings")


class YouTubeConnection(Base, TimestampMixin):
    """OAuth 2.0 connection to a YouTube channel.

    Tokens are sealed with Fernet before they reach this table; the plaintext never
    touches the database or the logs. NEXORA never asks for a YouTube password.
    """

    __tablename__ = "youtube_connections"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    youtube_channel_title: Mapped[str | None] = mapped_column(String(255))
    youtube_custom_url: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConnectionStatus.NOT_CONNECTED.value
    )
    scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at = utc_column()
    connected_at = utc_column()
    last_refreshed_at = utc_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    has_analytics_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_monetary_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    channel: Mapped[Channel] = relationship(back_populates="youtube_connection")


class OAuthState(Base):
    """Short-lived CSRF state for the YouTube OAuth authorization code flow."""

    __tablename__ = "oauth_states"

    id: Mapped[uuid.UUID] = uuid_pk()
    state_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    code_verifier_encrypted: Mapped[str | None] = mapped_column(Text)
    redirect_to: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    consumed_at = utc_column()
