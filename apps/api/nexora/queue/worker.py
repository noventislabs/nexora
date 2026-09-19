"""Background worker process.

Run with ``python -m nexora.queue.worker``. The worker blocks on a Redis wake-up but
also sweeps PostgreSQL on a timer, so it makes progress even if Redis restarts.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import uuid
from typing import Any

from nexora.core.errors import NexoraError, ProviderNotConfigured, UpstreamPermanentError
from nexora.core.logging import configure_logging, get_logger
from nexora.db.models import Job
from nexora.db.session import session_scope
from nexora.queue import handlers as _handlers  # noqa: F401  (registers job handlers)
from nexora.queue import jobs as job_queue
from nexora.queue.broker import wait_for_ready
from nexora.queue.types import get_handler

logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 5
REAP_INTERVAL_SECONDS = 300


class Worker:
    def __init__(self, queues: tuple[str, ...] = ("default", "media")):
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.queues = queues
        self._stop = threading.Event()
        self._last_reap = 0.0

    def request_stop(self, *_: Any) -> None:
        logger.info("worker.stop_requested", extra={"worker_id": self.id})
        self._stop.set()

    def run(self) -> None:
        logger.info(
            "worker.started",
            extra={"worker_id": self.id, "queues": list(self.queues), "pid": os.getpid()},
        )
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception as exc:  # a crash here must not kill the worker loop
                logger.exception("worker.loop_error", extra={"error": str(exc)})
                time.sleep(SWEEP_INTERVAL_SECONDS)
                continue
            if not processed:
                # Block briefly on Redis; falls through to the DB sweep on timeout.
                wait_for_ready(timeout=SWEEP_INTERVAL_SECONDS)
            self._maybe_reap()
        logger.info("worker.stopped", extra={"worker_id": self.id})

    def _maybe_reap(self) -> None:
        now = time.monotonic()
        if now - self._last_reap < REAP_INTERVAL_SECONDS:
            return
        self._last_reap = now
        try:
            with session_scope() as session:
                reaped = job_queue.reap_stalled(session)
            if reaped:
                logger.warning("worker.reaped_stalled_jobs", extra={"count": reaped})
        except Exception as exc:
            logger.warning("worker.reap_failed", extra={"error": str(exc)})

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True if work was done."""
        with session_scope() as session:
            job = job_queue.claim_next(session, self.id, queues=self.queues)
            if job is None:
                return False
            job_id, job_type = job.id, job.type
            payload = dict(job.payload or {})

        execute_job(job_id, job_type, payload, worker_id=self.id)
        return True


def execute_job(job_id: uuid.UUID, job_type: str, payload: dict[str, Any], *, worker_id: str) -> None:
    """Run one claimed job in its own transaction and record the outcome."""
    started = time.monotonic()
    handler = get_handler(job_type)
    if handler is None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job_queue.log(session, job, f"No handler registered for '{job_type}'.", level="ERROR")
                job_queue.fail(session, job, f"No handler registered for '{job_type}'.", permanent=True)
        return

    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            result = handler(session, job)
            job_queue.complete(session, job, result if isinstance(result, dict) else None)
    except ProviderNotConfigured as exc:
        # A missing integration is a configuration problem, not a transient fault.
        _record_failure(job_id, exc.message, permanent=True)
    except UpstreamPermanentError as exc:
        _record_failure(job_id, exc.message, permanent=True)
    except NexoraError as exc:
        _record_failure(job_id, exc.message, permanent=False)
    except Exception as exc:
        _record_failure(job_id, f"{type(exc).__name__}: {exc}", permanent=False)
    finally:
        logger.info(
            "job.finished",
            extra={
                "job_id": str(job_id),
                "job_type": job_type,
                "worker_id": worker_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )


def _record_failure(job_id: uuid.UUID, message: str, *, permanent: bool) -> None:
    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job_queue.log(session, job, message, level="ERROR")
            job_queue.fail(session, job, message, permanent=permanent)
    except Exception as exc:  # pragma: no cover - the DB is down; log and move on
        logger.exception("job.failure_record_error", extra={"job_id": str(job_id), "error": str(exc)})


def main() -> int:
    configure_logging()
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    worker.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
