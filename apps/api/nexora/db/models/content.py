"""Content projects, scripts, fact checks and metadata."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import CheckStatus, ProjectStatus, VideoFormat


class ContentProject(Base, TimestampMixin):
    """The unit of work that travels the whole pipeline."""

    __tablename__ = "content_projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_candidates.id", ondelete="SET NULL"), index=True
    )
    research_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_research.id", ondelete="SET NULL")
    )
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProjectStatus.DRAFT.value, index=True
    )
    video_format: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VideoFormat.LONG_FORM.value
    )
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=480)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")

    current_script_version_id: Mapped[uuid.UUID | None] = mapped_column()
    current_metadata_version_id: Mapped[uuid.UUID | None] = mapped_column()
    current_thumbnail_id: Mapped[uuid.UUID | None] = mapped_column()
    current_render_asset_id: Mapped[uuid.UUID | None] = mapped_column()
    narration_asset_id: Mapped[uuid.UUID | None] = mapped_column()

    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = utc_column()
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    last_error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class ContentScript(Base, TimestampMixin):
    """One script entity per project; every generation adds a ScriptVersion."""

    __tablename__ = "content_scripts"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")


class ScriptVersion(Base):
    """An immutable script revision. Nothing is ever overwritten."""

    __tablename__ = "script_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_scripts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Structured sections: hook, introduction, main sections, evidence, conclusion, CTA.
    sections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_references: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    provider: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(96))
    prompt_digest: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at = utc_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("script_id", "version", name="uq_script_versions_script_version"),)


class FactCheck(Base):
    """Claim-level verification run against the project's research sources."""

    __tablename__ = "fact_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    script_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_versions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CheckStatus.NOT_RUN.value, index=True
    )
    supported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unsupported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claims: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    contradictions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    provider: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(96))
    error: Mapped[str | None] = mapped_column(Text)
    created_at = utc_column(nullable=False)


class MetadataVersion(Base):
    """A YouTube metadata revision (title/description/tags) for a project."""

    __tablename__ = "metadata_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    category_id: Mapped[str | None] = mapped_column(String(16))
    default_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    made_for_kids: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(96))
    created_at = utc_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("content_project_id", "version", name="uq_metadata_versions_project_version"),
    )


class QualityCheck(Base):
    """Deterministic, explainable gate results (no model scoring theatre)."""

    __tablename__ = "quality_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CheckStatus.NOT_RUN.value)
    score: Mapped[int | None] = mapped_column(Integer)
    checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at = utc_column(nullable=False)


class CopyrightCheck(Base):
    """Licence audit across every asset that will appear in the render."""

    __tablename__ = "copyright_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    content_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CheckStatus.NOT_RUN.value)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    unknown_license_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prohibited_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at = utc_column(nullable=False)
