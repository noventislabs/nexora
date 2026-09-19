"""Publishing pipeline, published videos and analytics snapshots."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import AnalyticsScope, RunStatus


class PublishJob(Base, TimestampMixin):
    """One upload intent. ``idempotency_key`` makes re-delivery safe.

    A job never uploads twice: the key is unique, and the worker refuses to start a
    job that already carries a ``youtube_video_id``.
    """

    __tablename__ = "publish_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=RunStatus.QUEUED.value, index=True
    )
    scheduled_for = utc_column(index=True)
    privacy_status: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    publish_at_youtube = utc_column()

    authorized_by: Mapped[str | None] = mapped_column(String(16))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = utc_column()

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at = utc_column()
    permanent_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    youtube_video_id: Mapped[str | None] = mapped_column(String(64), index=True)
    upload_bytes: Mapped[int | None] = mapped_column(BigInteger)
    verified_at = utc_column()
    started_at = utc_column()
    finished_at = utc_column()
    preflight: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class YouTubeVideo(Base, TimestampMixin):
    """A video that actually exists on YouTube, confirmed by the API."""

    __tablename__ = "youtube_videos"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_projects.id", ondelete="SET NULL"), index=True
    )
    youtube_video_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    privacy_status: Mapped[str | None] = mapped_column(String(16))
    upload_status: Mapped[str | None] = mapped_column(String(32))
    published_at = utc_column(index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    last_synced_at = utc_column()

    __table_args__ = (
        UniqueConstraint("channel_id", "youtube_video_id", name="uq_youtube_videos_channel_video"),
    )


class AnalyticsSnapshot(Base):
    """An immutable capture of what an upstream analytics API returned.

    ``metrics`` holds only keys the API actually returned. A metric the connected
    scopes do not grant is absent, never zero.
    """

    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("youtube_videos.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default=AnalyticsScope.CHANNEL.value)
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    captured_at = utc_column(nullable=False, index=True)
    period_start = utc_column()
    period_end = utc_column()
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    unavailable_metrics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_analytics_snapshots_scope_captured", "channel_id", "scope", "captured_at"),
    )


class PerformanceObservation(Base):
    """A non-causal reading of a published video's measured performance.

    Wording is constrained by construction: every row is a *possible contributing
    factor*, never an assertion of cause.
    """

    __tablename__ = "performance_observations"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("youtube_videos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analytics_snapshots.id", ondelete="SET NULL")
    )
    signal: Mapped[str] = mapped_column(String(64), nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    possible_factors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    suggested_investigation: Mapped[str | None] = mapped_column(Text)
    measured: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at = utc_column(nullable=False)


class ContentPerformanceFeature(Base):
    """Historical features used by the learning loop.

    Only records with real measured outcomes are stored; a video without analytics
    contributes nothing rather than a guessed value.
    """

    __tablename__ = "content_performance_features"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("youtube_videos.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    topic_category: Mapped[str | None] = mapped_column(String(64), index=True)
    video_format: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    published_hour_local: Mapped[int | None] = mapped_column(Integer)
    published_weekday: Mapped[int | None] = mapped_column(Integer)
    title_length: Mapped[int | None] = mapped_column(Integer)
    title_has_number: Mapped[bool | None] = mapped_column(Boolean)
    title_has_question: Mapped[bool | None] = mapped_column(Boolean)
    thumbnail_generator: Mapped[str | None] = mapped_column(String(48))
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    ctr_percent: Mapped[float | None] = mapped_column(Float)
    average_view_duration_seconds: Mapped[float | None] = mapped_column(Float)
    average_view_percentage: Mapped[float | None] = mapped_column(Float)
    measured_at = utc_column()
    created_at = utc_column(nullable=False)
