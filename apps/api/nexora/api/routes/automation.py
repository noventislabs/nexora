"""Autopilot: the global kill switch, per-channel state and automation runs."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.core.errors import NotFound
from nexora.db.models import AutomationLock, AutomationRun, ContentProject
from nexora.services import channels as channel_service
from nexora.services import killswitch
from nexora.services.automation import dedupe, gates, locks, orchestrator
from nexora.services.automation.factgate import evaluate as evaluate_fact_gate

router = APIRouter(prefix="/api/automation", tags=["automation"])


class EmergencyStopRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=1000)


class RunRequest(ApiModel):
    channel_id: uuid.UUID
    background: bool = True


class DuplicateCheckRequest(ApiModel):
    channel_id: uuid.UUID
    title: str = Field(max_length=300)
    angle: str | None = Field(default=None, max_length=1000)


def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


# ----------------------------------------------------------- global kill switch
@router.get("/emergency-stop")
def get_emergency_stop(db: DbSession, current: AuthUser) -> dict[str, Any]:
    return killswitch.state(db).to_dict()


@router.post("/emergency-stop")
def engage_emergency_stop(
    payload: EmergencyStopRequest, db: DbSession, current: Writer
) -> dict[str, Any]:
    """Halt every channel of every user.

    Also cancels queued publishing work: a job already in the queue would otherwise
    upload after the stop was engaged, which is precisely what a stop is for.
    """
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import AUTOMATION_ADVANCE, AUTOMATION_TICK, YOUTUBE_UPLOAD

    state = killswitch.engage(db, reason=payload.reason, user_id=current.user.id)
    cancelled = job_queue.cancel_pending_globally(
        db, types=(YOUTUBE_UPLOAD, AUTOMATION_ADVANCE, AUTOMATION_TICK)
    )
    return {**state.to_dict(), "cancelled_jobs": cancelled}


@router.delete("/emergency-stop")
def release_emergency_stop(db: DbSession, current: Writer) -> dict[str, Any]:
    """Clear the global stop.

    This turns nothing back on. Every channel keeps the automation and publishing
    settings it had before, so releasing a stop can never be a way to enable autopilot.
    """
    state = killswitch.release(db, user_id=current.user.id)
    return {
        **state.to_dict(),
        "note": (
            "Released. No channel's automation or publishing settings were changed — "
            "anything that was off before is still off."
        ),
    }


# ------------------------------------------------------------- channel state
@router.get("/state")
def automation_state(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Everything governing automation for one channel, and what it may do now."""
    channel = _resolve_channel(db, current, channel_id)
    automation = channel_service.get_automation_settings(db, channel.id)
    state = gates.automation_state(db, channel, automation)

    live = locks.live_locks(db, channel.id)
    state["active_locks"] = [
        {
            "lock_key": lock.lock_key,
            "holder": lock.holder,
            "acquired_at": lock.acquired_at.isoformat() if lock.acquired_at else None,
            "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
        }
        for lock in live
    ]
    state["levels"] = {
        level: dict(capabilities)
        for level, capabilities in gates.LEVEL_CAPABILITIES.items()
    }
    return state


# ---------------------------------------------------------------------- runs
@router.post("/run", status_code=status.HTTP_201_CREATED)
def start_run(
    payload: RunRequest, db: DbSession, current: Writer, ctx: RequestContext
) -> dict[str, Any]:
    """Start one pipeline traversal for a channel.

    Refused while a stop is engaged or automation is off — the same server-side gates
    the scheduler goes through, because a manual trigger must not be a way around them.
    """
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import AUTOMATION_ADVANCE

    channel = channel_service.get_channel_for_user(db, current.user, payload.channel_id)
    automation = channel_service.get_automation_settings(db, channel.id)

    allowed = gates.can_automate(db, channel, automation)
    if not allowed.allowed:
        from nexora.core.errors import SafetyBlocked

        raise SafetyBlocked(
            "Automation cannot run for this channel: "
            + "; ".join(entry["detail"] for entry in allowed.blockers),
            details=allowed.to_dict(),
        )

    run = orchestrator.start_run(db, channel, trigger="manual", user_id=current.user.id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            AUTOMATION_ADVANCE,
            channel_id=channel.id,
            queue="media",
            idempotency_key=f"automation:{run.id}",
            payload={"automation_run_id": str(run.id), "user_id": str(current.user.id)},
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), **orchestrator.run_to_dict(run)}

    orchestrator.execute(db, channel, run, user_id=current.user.id)
    return {"mode": "inline", **orchestrator.run_to_dict(run)}


@router.get("/runs")
def list_runs(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    rows = list(
        db.execute(
            select(AutomationRun)
            .where(AutomationRun.channel_id == channel.id)
            .order_by(AutomationRun.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "items": [orchestrator.run_to_dict(row) for row in rows],
        "total": len(rows),
        "channel_id": str(channel.id),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: DbSession, current: AuthUser) -> dict[str, Any]:
    run = db.get(AutomationRun, run_id)
    if run is None:
        raise NotFound("Automation run not found.")
    # Ownership is checked through the channel, not assumed from the run id.
    channel_service.get_channel_for_user(db, current.user, run.channel_id)
    return orchestrator.run_to_dict(run)


# ---------------------------------------------------------------------- locks
@router.get("/locks")
def list_locks(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    rows = list(
        db.execute(
            select(AutomationLock)
            .where(AutomationLock.channel_id == channel.id)
            .order_by(AutomationLock.acquired_at.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "items": [
            {
                "id": str(lock.id),
                "lock_key": lock.lock_key,
                "holder": lock.holder,
                "acquired_at": lock.acquired_at.isoformat() if lock.acquired_at else None,
                "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
                "released_at": lock.released_at.isoformat() if lock.released_at else None,
                "release_reason": lock.release_reason,
                "live": lock.released_at is None,
            }
            for lock in rows
        ],
        "total": len(rows),
        "note": (
            "Locks are database rows with a unique constraint, not Redis keys. That is "
            "what guarantees two workers never build the same video."
        ),
    }


@router.post("/locks/sweep")
def sweep_locks(db: DbSession, current: Writer) -> dict[str, Any]:
    """Release locks whose holder never came back."""
    released = locks.release_expired(db)
    return {"released": released}


# ------------------------------------------------------------------ duplicates
@router.post("/duplicate-check")
def duplicate_check(
    payload: DuplicateCheckRequest, db: DbSession, current: AuthUser
) -> dict[str, Any]:
    """Would this topic repeat work this channel has already done?"""
    channel = channel_service.get_channel_for_user(db, current.user, payload.channel_id)
    return dedupe.check(
        db, channel, title=payload.title, angle=payload.angle
    ).to_dict()


@router.get("/fact-gate/{project_id}")
def fact_gate(
    project_id: uuid.UUID,
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """May automation publish this project's claims?"""
    channel = _resolve_channel(db, current, channel_id)
    project = db.get(ContentProject, project_id)
    if project is None or project.channel_id != channel.id:
        raise NotFound("Content project not found for this channel.")
    automation = channel_service.get_automation_settings(db, channel.id)
    return {
        "content_project_id": str(project.id),
        "editorial_format": project.editorial_format,
        **evaluate_fact_gate(db, project, automation).to_dict(),
    }
