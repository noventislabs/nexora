"""Job queue, automation runs, audit log and system settings."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import ActorType, RunStatus


class Job(Base, TimestampMixin):
    """Durable background job record.

    PostgreSQL is the source of truth for job state; Redis only carries the wake-up
    signal. A lost Redis message therefore delays a job, it never loses it — the
    worker also sweeps the table for due work.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, default="default", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RunStatus.QUEUED.value, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at = utc_column(nullable=False, index=True)

    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    content_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_projects.id", ondelete="CASCADE"), index=True
    )
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)

    worker_id: Mapped[str | None] = mapped_column(String(96))
    heartbeat_at = utc_column()
    started_at = utc_column()
    finished_at = utc_column()
    error: Mapped[str | None] = mapped_column(Text)
    permanent_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_jobs_claimable", "status", "available_at", "priority"),)


class JobLog(Base):
    """Per-job log line, kept in PostgreSQL so job history survives a Redis flush."""

    __tablename__ = "job_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at = utc_column(nullable=False, index=True)


class AutomationRun(Base):
    """One traversal of the pipeline, whether operator-triggered or scheduled."""

    __tablename__ = "automation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_projects.id", ondelete="SET NULL"), index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RunStatus.QUEUED.value, index=True
    )
    current_stage: Mapped[str | None] = mapped_column(String(48))
    stages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    stopped_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at = utc_column()
    finished_at = utc_column()
    created_at = utc_column(nullable=False)


class AuditLog(Base):
    """Append-only record of every state transition that matters."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default=ActorType.USER.value)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at = utc_column(nullable=False, index=True)


class SystemSetting(Base, TimestampMixin):
    """Global key/value configuration that operators can change at runtime."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
