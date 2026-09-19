"""Durable job queue: claiming, retries, backoff, idempotency and cancellation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nexora.core.errors import NexoraError, ProviderNotConfigured
from nexora.db.models import Channel, Job, JobLog
from nexora.queue import jobs as job_queue
from nexora.queue.types import (
    ALL_JOB_TYPES,
    ANALYTICS_SYNC,
    TREND_SCAN,
    YOUTUBE_UPLOAD,
    get_handler,
    register_handler,
)
from nexora.queue.worker import Worker, execute_job


def test_every_spec_job_type_exists() -> None:
    required = {
        "trend_scan", "topic_generation", "research", "script_generation", "fact_check",
        "voice_generation", "asset_collection", "video_render", "thumbnail_generation",
        "quality_check", "youtube_upload", "analytics_sync",
    }
    assert required <= set(ALL_JOB_TYPES)


def test_enqueue_creates_queued_job(db: Session, channel: Channel) -> None:
    job = job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id, payload={"limit": 10})
    db.commit()
    assert job.status == "QUEUED"
    assert job.attempts == 0
    assert job.payload == {"limit": 10}
    assert job.max_attempts == 3


def test_idempotency_key_prevents_duplicate_jobs(db: Session, channel: Channel) -> None:
    first = job_queue.enqueue(db, YOUTUBE_UPLOAD, channel_id=channel.id, idempotency_key="upload-1")
    db.commit()
    second = job_queue.enqueue(db, YOUTUBE_UPLOAD, channel_id=channel.id, idempotency_key="upload-1")
    db.commit()
    assert first.id == second.id
    assert db.query(Job).count() == 1


def test_claim_next_marks_running_and_increments_attempts(db: Session, channel: Channel) -> None:
    job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id)
    db.commit()

    claimed = job_queue.claim_next(db, "worker-1")
    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert claimed.attempts == 1
    assert claimed.worker_id == "worker-1"
    db.commit()

    # Nothing left to claim.
    assert job_queue.claim_next(db, "worker-2") is None


def test_future_jobs_are_not_claimed_early(db: Session, channel: Channel) -> None:
    job_queue.enqueue(
        db, TREND_SCAN, channel_id=channel.id, available_at=datetime.now(UTC) + timedelta(hours=1)
    )
    db.commit()
    assert job_queue.claim_next(db, "worker-1") is None


def test_priority_orders_claims(db: Session, channel: Channel) -> None:
    job_queue.enqueue(db, ANALYTICS_SYNC, channel_id=channel.id, priority=200)
    urgent = job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id, priority=10)
    db.commit()
    claimed = job_queue.claim_next(db, "worker-1")
    assert claimed is not None and claimed.id == urgent.id


def test_failure_schedules_bounded_exponential_retry(db: Session, channel: Channel) -> None:
    job = job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id)
    db.commit()

    delays = []
    for _ in range(3):
        claimed = job_queue.claim_next(db, "worker-1")
        assert claimed is not None
        before = datetime.now(UTC)
        job_queue.fail(db, claimed, "upstream timeout")
        db.commit()
        if claimed.status == "QUEUED":
            delays.append((claimed.available_at - before).total_seconds())
            claimed.available_at = datetime.now(UTC)
            db.commit()

    db.refresh(job)
    assert job.status == "FAILED"
    assert job.attempts == 3
    assert job.permanent_failure is True
    # Backoff grows, it does not stay flat.
    assert delays == sorted(delays) and delays[0] < delays[-1]


def test_permanent_failure_is_not_retried(db: Session, channel: Channel) -> None:
    job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id)
    db.commit()
    claimed = job_queue.claim_next(db, "worker-1")
    assert claimed is not None
    job_queue.fail(db, claimed, "invalid credentials", permanent=True)
    db.commit()

    assert claimed.status == "FAILED"
    assert claimed.permanent_failure is True
    assert job_queue.claim_next(db, "worker-2") is None


def test_backoff_is_bounded() -> None:
    assert job_queue.backoff_for(1) < job_queue.backoff_for(2) < job_queue.backoff_for(3)
    assert job_queue.backoff_for(99).total_seconds() <= job_queue.MAX_BACKOFF_SECONDS


def test_cancel_removes_queued_job(db: Session, channel: Channel) -> None:
    job = job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id)
    db.commit()
    job_queue.cancel(db, job, "operator")
    db.commit()
    assert job.status == "CANCELLED"
    assert job_queue.claim_next(db, "worker-1") is None


def test_reap_stalled_returns_job_to_queue(db: Session, channel: Channel) -> None:
    job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id)
    db.commit()
    claimed = job_queue.claim_next(db, "dead-worker")
    assert claimed is not None
    claimed.heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()

    assert job_queue.reap_stalled(db) == 1
    db.commit()
    db.refresh(claimed)
    assert claimed.status == "QUEUED"
    assert "stopped responding" in (claimed.error or "")


def test_worker_runs_registered_handler(db: Session, channel: Channel) -> None:
    calls: list[str] = []

    @register_handler(ANALYTICS_SYNC)
    def _handler(session, job):  # noqa: ANN001, ANN202
        calls.append(str(job.id))
        return {"synced": True}

    try:
        job = job_queue.enqueue(db, ANALYTICS_SYNC, channel_id=channel.id)
        db.commit()
        job_id = job.id

        worker = Worker()
        assert worker.run_once() is True

        db.expire_all()
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == "SUCCESS"
        assert refreshed.result == {"synced": True}
        assert calls == [str(job_id)]
    finally:
        from nexora.queue import types as job_types

        job_types._handlers.pop(ANALYTICS_SYNC, None)


def test_missing_handler_fails_permanently(db: Session, channel: Channel) -> None:
    job = job_queue.enqueue(db, "asset_collection", channel_id=channel.id)
    db.commit()
    claimed = job_queue.claim_next(db, "worker-1")
    assert claimed is not None
    db.commit()

    assert get_handler("asset_collection") is None
    execute_job(job.id, "asset_collection", {}, worker_id="worker-1")

    db.expire_all()
    refreshed = db.get(Job, job.id)
    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.permanent_failure is True
    assert db.query(JobLog).filter(JobLog.job_id == job.id).count() == 1


def test_not_configured_provider_is_a_permanent_failure(db: Session, channel: Channel) -> None:
    """A missing integration is a configuration problem — retrying cannot fix it."""

    @register_handler(ANALYTICS_SYNC)
    def _handler(session, job):  # noqa: ANN001, ANN202
        raise ProviderNotConfigured("YouTube is NOT CONFIGURED.")

    try:
        job = job_queue.enqueue(db, ANALYTICS_SYNC, channel_id=channel.id)
        db.commit()
        execute_job(job.id, ANALYTICS_SYNC, {}, worker_id="worker-1")

        db.expire_all()
        refreshed = db.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.status == "FAILED"
        assert refreshed.permanent_failure is True
        assert "NOT CONFIGURED" in (refreshed.error or "")
    finally:
        from nexora.queue import types as job_types

        job_types._handlers.pop(ANALYTICS_SYNC, None)


def test_transient_error_is_retried(db: Session, channel: Channel) -> None:
    @register_handler(ANALYTICS_SYNC)
    def _handler(session, job):  # noqa: ANN001, ANN202
        raise NexoraError("temporary upstream hiccup")

    try:
        job = job_queue.enqueue(db, ANALYTICS_SYNC, channel_id=channel.id)
        db.commit()
        claimed = job_queue.claim_next(db, "worker-1")
        assert claimed is not None
        db.commit()
        execute_job(job.id, ANALYTICS_SYNC, {}, worker_id="worker-1")

        db.expire_all()
        refreshed = db.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.status == "QUEUED"
        assert refreshed.permanent_failure is False
        assert refreshed.available_at > datetime.now(UTC)
    finally:
        from nexora.queue import types as job_types

        job_types._handlers.pop(ANALYTICS_SYNC, None)


def test_unknown_job_type_is_rejected(db: Session, channel: Channel) -> None:
    with pytest.raises(NexoraError):
        job_queue.enqueue(db, "definitely_not_a_job", channel_id=channel.id)
