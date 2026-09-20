"""Background handlers for the autopilot.

Two jobs:

* ``AUTOMATION_TICK`` — the scheduler's heartbeat. Decides which channels are due and
  queues a run for each. Deliberately does no pipeline work itself, so one slow render
  cannot delay every other channel's tick.
* ``AUTOMATION_ADVANCE`` — executes one channel's run.

Both re-check the stops before doing anything. A job queued five minutes before an
emergency stop must not run after it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound
from nexora.core.logging import get_logger
from nexora.db.models import AutomationRun, AutomationSettings, Channel, Job
from nexora.queue import jobs as job_queue
from nexora.queue.types import AUTOMATION_ADVANCE, AUTOMATION_TICK, register_handler
from nexora.services import killswitch
from nexora.services.automation import gates, locks, orchestrator

logger = get_logger(__name__)


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


@register_handler(AUTOMATION_TICK)
def handle_tick(session: Session, job: Job) -> dict[str, Any]:
    """Queue a run for every channel that is due.

    Returns a per-channel account of what it did and why, so an operator reading the
    job log can see that a channel was considered and skipped rather than forgotten.
    """
    # Sweeping first means a worker that died an hour ago does not keep its channel
    # locked out of today's run.
    swept = locks.release_expired(session)

    if killswitch.is_engaged(session):
        return {
            "queued": 0,
            "skipped": "global_emergency_stop",
            "expired_locks_released": swept,
            "detail": "The global emergency stop is engaged; no runs were queued.",
        }

    now = datetime.now(UTC)
    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    rows = session.execute(
        select(Channel, AutomationSettings)
        .join(AutomationSettings, AutomationSettings.channel_id == Channel.id)
        .where(Channel.is_active.is_(True), AutomationSettings.automation_enabled.is_(True))
    ).all()

    for channel, automation in rows:
        allowed = gates.can_automate(session, channel, automation)
        if not allowed.allowed:
            skipped.append(
                {
                    "channel_id": str(channel.id),
                    "reason": "; ".join(entry["detail"] for entry in allowed.blockers),
                }
            )
            continue

        due, detail = _is_due(session, channel, automation, now=now)
        if not due:
            skipped.append({"channel_id": str(channel.id), "reason": detail})
            continue

        run = orchestrator.start_run(session, channel, trigger="schedule")
        queued_job = job_queue.enqueue(
            session,
            AUTOMATION_ADVANCE,
            channel_id=channel.id,
            queue="media",
            # One run per automation_run row, whatever the queue does with retries.
            idempotency_key=f"automation:{run.id}",
            payload={"automation_run_id": str(run.id), "user_id": str(channel.user_id)},
        )
        session.flush()
        job_queue.signal(queued_job)
        queued.append({"channel_id": str(channel.id), "automation_run_id": str(run.id)})

    return {
        "queued": len(queued),
        "runs": queued,
        "skipped": skipped,
        "expired_locks_released": swept,
    }


@register_handler(AUTOMATION_ADVANCE)
def handle_advance(session: Session, job: Job) -> dict[str, Any]:
    """Execute one channel's run."""
    payload = job.payload or {}
    run_id = _uuid(payload.get("automation_run_id"))
    if run_id is None:
        raise NotFound("This automation job names no run.")

    run = session.get(AutomationRun, run_id)
    if run is None:
        raise NotFound("The automation run no longer exists.")

    channel = session.get(Channel, run.channel_id)
    if channel is None:
        raise NotFound("The channel for this automation run no longer exists.")

    # The job carries both ids and they are checked against each other here, so a
    # tampered channel_id cannot borrow another user's OAuth connection.
    gates.require_ownership(session, channel.user_id, channel)

    if run.status not in {"QUEUED", "RUNNING"}:
        return {
            "automation_run_id": str(run.id),
            "skipped": True,
            "detail": f"This run already finished as {run.status}.",
        }

    orchestrator.execute(session, channel, run, user_id=None)
    return {
        "automation_run_id": str(run.id),
        "status": run.status,
        "stopped_reason": run.stopped_reason,
        "content_project_id": str(run.content_project_id) if run.content_project_id else None,
        "stages": [entry.get("stage") for entry in run.stages or []],
    }


def _is_due(
    session: Session,
    channel: Channel,
    automation: AutomationSettings,
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Whether this channel should start a run at this moment.

    Three reasons not to: it already has one in flight, it ran recently, or it is
    outside its configured hour. None of them is an error.
    """
    live = [
        lock
        for lock in locks.live_locks(session, channel.id)
        if lock.lock_key == locks.CHANNEL_RUN_KEY
    ]
    if live:
        return False, "A run is already in progress for this channel."

    counts = gates.daily_counts(session, channel, automation, now=now)
    if counts.published >= automation.max_videos_per_day:
        return False, (
            f"Already published {counts.published} today ({counts.day}, {counts.timezone}); "
            f"the limit is {automation.max_videos_per_day}."
        )

    recent = session.execute(
        select(AutomationRun)
        .where(
            AutomationRun.channel_id == channel.id,
            AutomationRun.created_at >= now - timedelta(minutes=automation.min_interval_minutes),
        )
        .limit(1)
    ).scalar_one_or_none()
    if recent is not None:
        return False, (
            f"A run started within the last {automation.min_interval_minutes} minutes."
        )

    _, _, _, zone = gates.channel_day_bounds(automation, now=now)
    from zoneinfo import ZoneInfo

    local_hour = now.astimezone(ZoneInfo(zone)).hour
    if not automation.daily_scan_enabled:
        return True, "Due."
    if local_hour < automation.daily_scan_hour:
        return False, (
            f"This channel runs at {automation.daily_scan_hour}:00 {zone}; it is "
            f"{local_hour}:00 there now."
        )
    return True, "Due."
