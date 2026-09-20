"""Durable job queue backed by PostgreSQL with Redis wake-ups.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so several workers can share a
queue without double-processing. Retries are bounded and exponential; a failure marked
permanent is never retried.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import NexoraError
from nexora.core.logging import get_logger
from nexora.db.models import Job, JobLog
from nexora.db.models.enums import RunStatus
from nexora.queue.broker import notify_ready
from nexora.queue.types import ALL_JOB_TYPES

logger = get_logger(__name__)

#: Retry backoff in seconds, indexed by attempt number (1-based).
BACKOFF_SECONDS = (30, 120, 600)
MAX_BACKOFF_SECONDS = 3600


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_for(attempt: int) -> timedelta:
    index = min(max(attempt, 1), len(BACKOFF_SECONDS)) - 1
    seconds = BACKOFF_SECONDS[index] if attempt <= len(BACKOFF_SECONDS) else MAX_BACKOFF_SECONDS
    return timedelta(seconds=seconds)


def enqueue(
    session: Session,
    job_type: str,
    *,
    payload: dict[str, Any] | None = None,
    channel_id: uuid.UUID | None = None,
    content_project_id: uuid.UUID | None = None,
    automation_run_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    available_at: datetime | None = None,
    priority: int = 100,
    max_attempts: int | None = None,
    queue: str = "default",
) -> Job:
    """Create (or return the existing) job for ``idempotency_key``.

    Re-enqueueing with the same key is a no-op that returns the original job, which is
    what makes upload retries safe.
    """
    if job_type not in ALL_JOB_TYPES:
        raise NexoraError(f"Unknown job type '{job_type}'.")

    if idempotency_key:
        existing = session.execute(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    job = Job(
        type=job_type,
        queue=queue,
        payload=payload or {},
        status=RunStatus.QUEUED.value,
        priority=priority,
        attempts=0,
        max_attempts=max_attempts or settings.job_max_retries,
        available_at=available_at or utcnow(),
        channel_id=channel_id,
        content_project_id=content_project_id,
        automation_run_id=automation_run_id,
        idempotency_key=idempotency_key,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = session.execute(
                select(Job).where(Job.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        raise

    job_id = str(job.id)
    logger.info("job.enqueued", extra={"job_id": job_id, "job_type": job_type})
    return job


def signal(job: Job) -> None:
    """Wake a worker for a job that is due now."""
    if job.available_at is None or job.available_at <= utcnow():
        notify_ready(str(job.id))


def claim_next(session: Session, worker_id: str, *, queues: tuple[str, ...] = ("default",)) -> Job | None:
    """Atomically take the highest-priority due job, or return ``None``."""
    now = utcnow()
    stmt = (
        select(Job)
        .where(
            Job.status == RunStatus.QUEUED.value,
            Job.available_at <= now,
            Job.queue.in_(queues),
            Job.cancel_requested.is_(False),
        )
        .order_by(Job.priority.asc(), Job.available_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        return None

    job.status = RunStatus.RUNNING.value
    job.attempts += 1
    job.worker_id = worker_id
    job.started_at = now
    job.heartbeat_at = now
    session.flush()
    return job


def heartbeat(session: Session, job: Job) -> None:
    session.execute(update(Job).where(Job.id == job.id).values(heartbeat_at=utcnow()))


def complete(session: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = RunStatus.SUCCESS.value
    job.result = result or {}
    job.error = None
    job.finished_at = utcnow()
    session.flush()
    logger.info("job.succeeded", extra={"job_id": str(job.id), "job_type": job.type})


def fail(session: Session, job: Job, error: str, *, permanent: bool = False) -> None:
    """Record a failure and schedule a bounded retry unless it is permanent."""
    job.error = error[:4000]
    job.finished_at = utcnow()
    retryable = not permanent and job.attempts < job.max_attempts
    if retryable:
        job.status = RunStatus.QUEUED.value
        job.available_at = utcnow() + backoff_for(job.attempts)
        job.worker_id = None
        job.started_at = None
        job.finished_at = None
    else:
        job.status = RunStatus.FAILED.value
        job.permanent_failure = permanent or job.attempts >= job.max_attempts
    session.flush()
    logger.warning(
        "job.failed",
        extra={
            "job_id": str(job.id),
            "job_type": job.type,
            "attempt": job.attempts,
            "permanent": not retryable,
            "will_retry_at": job.available_at.isoformat() if retryable else None,
        },
    )


def cancel(session: Session, job: Job, reason: str = "cancelled by operator") -> None:
    if job.status in (RunStatus.SUCCESS.value, RunStatus.FAILED.value):
        return
    job.cancel_requested = True
    if job.status == RunStatus.QUEUED.value:
        job.status = RunStatus.CANCELLED.value
        job.finished_at = utcnow()
        job.error = reason
    session.flush()


def cancel_pending_for_channel(session: Session, channel_id: uuid.UUID, *, types: tuple[str, ...] = ()) -> int:
    """Cancel queued work for a channel. Used by the emergency stop."""
    stmt = select(Job).where(
        Job.channel_id == channel_id, Job.status == RunStatus.QUEUED.value
    )
    if types:
        stmt = stmt.where(Job.type.in_(types))
    jobs = list(session.execute(stmt).scalars())
    for job in jobs:
        cancel(session, job, reason="emergency stop")
    return len(jobs)


def cancel_pending_globally(session: Session, *, types: tuple[str, ...] = ()) -> int:
    """Cancel queued work across every channel. Used by the global emergency stop.

    A job already sitting in the queue would otherwise run after the stop was engaged,
    which is exactly what the stop exists to prevent. Only QUEUED jobs are touched — a
    job already executing is stopped by the orchestrator's own per-stage guard rather
    than by yanking its row out from under it.
    """
    stmt = select(Job).where(Job.status == RunStatus.QUEUED.value)
    if types:
        stmt = stmt.where(Job.type.in_(types))
    jobs = list(session.execute(stmt).scalars())
    for job in jobs:
        cancel(session, job, reason="global emergency stop")
    return len(jobs)


def log(session: Session, job: Job, message: str, *, level: str = "INFO", **context: Any) -> None:
    session.add(
        JobLog(
            job_id=job.id,
            level=level,
            message=message[:8000],
            context=context,
            created_at=utcnow(),
        )
    )


def reap_stalled(session: Session, *, stale_after_seconds: int = 1800) -> int:
    """Return jobs whose worker died back to the queue."""
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    stalled = list(
        session.execute(
            select(Job).where(
                Job.status == RunStatus.RUNNING.value,
                Job.heartbeat_at.is_not(None),
                Job.heartbeat_at < cutoff,
            )
        ).scalars()
    )
    for job in stalled:
        fail(session, job, f"Worker stopped responding (no heartbeat since {job.heartbeat_at}).")
    return len(stalled)
