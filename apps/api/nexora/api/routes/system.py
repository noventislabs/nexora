"""System health, capability reporting and job/log observability."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from nexora.api.deps import AuthUser, DbSession
from nexora.db.models import Job, JobLog
from nexora.db.models.enums import RunStatus
from nexora.queue import jobs as job_queue
from nexora.services import availability as avail
from nexora.services.health import system_health

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    """Live component status. Every value comes from a real probe or a real setting."""
    return system_health()


@router.get("/capabilities")
def capabilities(current: AuthUser) -> dict[str, Any]:
    """What this deployment can actually do right now.

    The frontend uses this to render NOT CONFIGURED states instead of dead buttons.
    """
    return {
        "llm": avail.llm_availability().to_dict(),
        "voice": avail.voice_availability().to_dict(),
        "youtube_oauth": avail.youtube_oauth_availability().to_dict(),
        "youtube_data_api": avail.youtube_data_api_availability().to_dict(),
        "reddit": avail.reddit_availability().to_dict(),
        "storage": avail.storage_availability().to_dict(),
        "encryption": avail.encryption_availability().to_dict(),
        "ffmpeg": _ffmpeg_capability(),
    }


def _ffmpeg_capability() -> dict[str, Any]:
    from nexora.db.models.enums import ComponentStatus
    from nexora.services.ffmpeg_runtime import ffmpeg_info

    info = ffmpeg_info()
    return {
        "status": (ComponentStatus.AVAILABLE if info.available else ComponentStatus.UNAVAILABLE).value,
        "provider": "ffmpeg",
        "detail": info.detail,
        "missing_settings": [] if info.available else ["FFMPEG_BINARY"],
        "metadata": {"version": info.version},
    }


jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "type": job.type,
        "queue": job.queue,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "permanent_failure": job.permanent_failure,
        "channel_id": str(job.channel_id) if job.channel_id else None,
        "content_project_id": str(job.content_project_id) if job.content_project_id else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "available_at": job.available_at.isoformat() if job.available_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "result": job.result,
    }


@jobs_router.get("")
def list_jobs(
    db: DbSession,
    current: AuthUser,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    job_type: Annotated[str | None, Query(alias="type", max_length=64)] = None,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conditions = []
    if status_filter:
        conditions.append(Job.status == status_filter.upper())
    if job_type:
        conditions.append(Job.type == job_type)
    if channel_id:
        conditions.append(Job.channel_id == channel_id)

    total = db.execute(select(func.count()).select_from(Job).where(*conditions)).scalar_one()
    rows = list(
        db.execute(
            select(Job)
            .where(*conditions)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [_job_dict(job) for job in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@jobs_router.get("/summary")
def job_summary(db: DbSession, current: AuthUser) -> dict[str, Any]:
    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    counts = {status.value: 0 for status in RunStatus}
    for status_value, count in rows:
        counts[status_value] = count
    from nexora.queue.broker import queue_depth

    return {"counts": counts, "redis_ready_depth": queue_depth()}


@jobs_router.get("/{job_id}")
def get_job(job_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    from nexora.core.errors import NotFound

    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found.")
    logs = list(
        db.execute(
            select(JobLog).where(JobLog.job_id == job.id).order_by(JobLog.created_at.asc()).limit(200)
        ).scalars()
    )
    return {
        **_job_dict(job),
        "logs": [
            {
                "level": entry.level,
                "message": entry.message,
                "context": entry.context,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in logs
        ],
    }


@jobs_router.post("/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    from nexora.core.errors import NotFound

    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found.")
    job_queue.cancel(db, job)
    return _job_dict(job)


@jobs_router.post("/{job_id}/retry")
def retry_job(job_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    """Re-queue a failed job for one more bounded run."""
    from datetime import UTC, datetime

    from nexora.core.errors import Conflict, NotFound

    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found.")
    if job.status not in (RunStatus.FAILED.value, RunStatus.CANCELLED.value):
        raise Conflict(f"Only FAILED or CANCELLED jobs can be retried (this one is {job.status}).")
    job.status = RunStatus.QUEUED.value
    job.permanent_failure = False
    job.cancel_requested = False
    job.max_attempts = job.attempts + 1
    job.available_at = datetime.now(UTC)
    job.error = None
    job.finished_at = None
    db.flush()
    job_queue.signal(job)
    return _job_dict(job)


logs_router = APIRouter(prefix="/api/logs", tags=["logs"])


@logs_router.get("/audit")
def audit_log(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    from nexora.db.models import AuditLog
    from nexora.services import audit as audit_service

    conditions = [AuditLog.channel_id == channel_id] if channel_id else []
    total = db.execute(select(func.count()).select_from(AuditLog).where(*conditions)).scalar_one()
    entries = audit_service.recent(db, channel_id=channel_id, limit=limit, offset=offset)
    return {
        "items": [
            {
                "id": str(entry.id),
                "actor_type": entry.actor_type,
                "action": entry.action,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "summary": entry.summary,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in entries
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
