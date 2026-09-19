"""Voice jobs, assets, video projects, render jobs and thumbnails."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import LicenseStatus, RunStatus


class VideoAsset(Base, TimestampMixin):
    """Every byte that can end up in a render, with its provenance.

    ``license_status`` defaults to ``LICENSE UNKNOWN``. Unknown-licence assets block
    autonomous publishing unless the channel explicitly opts out.
    """

    __tablename__ = "video_assets"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_projects.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    source: Mapped[str] = mapped_column(String(48), nullable=False, default="upload")
    source_url: Mapped[str | None] = mapped_column(Text)
    source_provider: Mapped[str | None] = mapped_column(String(64))
    license_type: Mapped[str | None] = mapped_column(String(96))
    license_url: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LicenseStatus.UNKNOWN.value, index=True
    )
    attribution: Mapped[str | None] = mapped_column(Text)
    usage_permission_note: Mapped[str | None] = mapped_column(Text)
    acquired_at = utc_column()

    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class VoiceJob(Base):
    """A narration synthesis attempt against a configured voice provider."""

    __tablename__ = "voice_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    script_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_versions.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    provider: Mapped[str | None] = mapped_column(String(48))
    voice_id: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.QUEUED.value)
    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    character_count: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at = utc_column(nullable=False)
    started_at = utc_column()
    finished_at = utc_column()


class VideoProject(Base, TimestampMixin):
    """The visual plan for a content project: format, template and scene list."""

    __tablename__ = "video_projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False, default="16:9")
    resolution: Mapped[str] = mapped_column(String(16), nullable=False, default="1080p")
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="narrated_explainer")
    scene_plan: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    subtitle_burn_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    music_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")


class VideoRenderJob(Base):
    """One FFmpeg render attempt, with the exact toolchain it used."""

    __tablename__ = "video_render_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    video_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.QUEUED.value)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    subtitle_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    ffmpeg_version: Mapped[str | None] = mapped_column(String(128))
    command_digest: Mapped[str | None] = mapped_column(String(64))
    log_excerpt: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at = utc_column()
    finished_at = utc_column()
    created_at = utc_column(nullable=False)


class Thumbnail(Base):
    """A generated or uploaded thumbnail awaiting a human decision."""

    __tablename__ = "thumbnails"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    generator: Mapped[str] = mapped_column(String(48), nullable=False, default="composite")
    concept: Mapped[str | None] = mapped_column(Text)
    headline: Mapped[str | None] = mapped_column(String(120))
    prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="generated", index=True)
    decided_at = utc_column()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at = utc_column(nullable=False)
