"""Background handlers for research, script generation and fact checking."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound
from nexora.db.models import Channel, ContentProject, Job, TopicCandidate
from nexora.db.models.enums import ActorType, ProjectStatus
from nexora.queue import jobs as job_queue
from nexora.queue.types import FACT_CHECK, RESEARCH, SCRIPT_GENERATION, register_handler
from nexora.services import projects as project_service
from nexora.services.factcheck import run_fact_check
from nexora.services.research.engine import run_research
from nexora.services.scripts import generate_script


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _channel(session: Session, job: Job) -> Channel:
    channel_id = job.channel_id or _uuid((job.payload or {}).get("channel_id"))
    if channel_id is None:
        raise NotFound("This job has no channel to work on.")
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFound(f"Channel {channel_id} no longer exists.")
    return channel


def _project(session: Session, job: Job) -> ContentProject:
    project_id = job.content_project_id or _uuid((job.payload or {}).get("content_project_id"))
    if project_id is None:
        raise NotFound("This job has no content project to work on.")
    project = session.get(ContentProject, project_id)
    if project is None:
        raise NotFound(f"Content project {project_id} no longer exists.")
    return project


@register_handler(RESEARCH)
def handle_research(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    payload = job.payload or {}
    candidate_id = _uuid(payload.get("topic_candidate_id"))
    if candidate_id is None:
        raise NotFound("This research job has no topic candidate.")
    candidate = session.get(TopicCandidate, candidate_id)
    if candidate is None or candidate.channel_id != channel.id:
        raise NotFound("Topic candidate not found for this channel.")

    job_queue.log(session, job, f"Researching '{candidate.title[:100]}'.")
    result = run_research(
        session,
        channel,
        candidate,
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
        user_id=_uuid(payload.get("user_id")),
        force=bool(payload.get("force")),
    )
    for note in result.dropped:
        job_queue.log(session, job, f"Dropped unsourced content: {note}", level="WARNING")
    job_queue.log(
        session,
        job,
        f"{result.research.document_count} document(s); "
        f"{len(result.research.key_facts or [])} fact(s), "
        f"{len(result.research.conflicts or [])} conflict(s).",
    )
    return result.to_dict()


@register_handler(SCRIPT_GENERATION)
def handle_script_generation(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    job_queue.log(session, job, f"Generating script for '{project.title[:100]}'.")
    result = generate_script(
        session,
        channel,
        project,
        guidance=payload.get("guidance"),
        user_id=_uuid(payload.get("user_id")),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
    )
    for note in result.dropped:
        job_queue.log(session, job, note, level="WARNING")
    job_queue.log(
        session,
        job,
        f"Script v{result.version.version}: {result.version.word_count} words, "
        f"~{result.version.estimated_duration_seconds}s, "
        f"originality {result.originality['score']}/100.",
    )
    return result.to_dict()


@register_handler(FACT_CHECK)
def handle_fact_check(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    version = project_service.current_version(session, project)
    if version is None:
        raise NotFound("This project has no current script version to fact check.")

    job_queue.log(session, job, f"Fact checking script v{version.version}.")
    result = run_fact_check(
        session,
        channel,
        project,
        version,
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
        user_id=_uuid(payload.get("user_id")),
    )

    # A failing check must never advance the pipeline on its own.
    if result.check.status == "PASS":
        project_service.advance_status(
            session, project, ProjectStatus.VOICE, note="Fact check passed."
        )
    else:
        job_queue.log(
            session,
            job,
            f"Fact check returned {result.check.status}; the project stays at "
            f"'{project.status}' for operator review.",
            level="WARNING",
        )
    return result.to_dict()
