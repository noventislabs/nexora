"""Background handlers for narration, rendering and thumbnails."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound
from nexora.db.models import Channel, ContentProject, Job
from nexora.db.models.enums import ActorType, ProjectStatus
from nexora.queue import jobs as job_queue
from nexora.queue.types import (
    THUMBNAIL_GENERATION,
    VIDEO_RENDER,
    VOICE_GENERATION,
    register_handler,
)
from nexora.services import projects as project_service
from nexora.services import thumbnails as thumbnail_service
from nexora.services import voice as voice_service
from nexora.services.video.render import render_project


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


@register_handler(VOICE_GENERATION)
def handle_voice_generation(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    version = project_service.current_version(session, project)
    if version is None:
        raise NotFound("This project has no current script version to narrate.")

    job_queue.log(session, job, f"Synthesizing narration for script v{version.version}.")
    result = voice_service.generate_narration(
        session,
        channel,
        project,
        version,
        voice_id=payload.get("voice_id"),
        user_id=_uuid(payload.get("user_id")),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
    )
    for warning in result.warnings:
        job_queue.log(session, job, warning, level="WARNING")
    job_queue.log(
        session,
        job,
        f"{result.job.duration_seconds:.1f}s measured over {result.chunks} request(s); "
        f"subtitle timing {result.job.timing_source}.",
    )
    project_service.advance_status(
        session, project, ProjectStatus.ASSETS, note="Narration produced."
    )
    return result.to_dict()


@register_handler(VIDEO_RENDER)
def handle_video_render(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    job_queue.log(session, job, f"Rendering '{project.title[:100]}'.")
    project_service.advance_status(
        session, project, ProjectStatus.RENDERING, note="Render started."
    )
    result = render_project(
        session,
        channel,
        project,
        burn_subtitles=payload.get("burn_subtitles"),
        user_id=_uuid(payload.get("user_id")),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
    )
    for warning in result.warnings:
        job_queue.log(session, job, warning, level="WARNING")
    job_queue.log(
        session,
        job,
        f"Rendered {result.job.resolution}, {result.duration_seconds:.1f}s measured, "
        f"{result.job.output_bytes} bytes.",
    )
    project_service.advance_status(
        session, project, ProjectStatus.READY, note="Render complete; awaiting review."
    )
    return result.to_dict()


@register_handler(THUMBNAIL_GENERATION)
def handle_thumbnail_generation(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    job_queue.log(session, job, "Generating thumbnail.")
    result = thumbnail_service.generate(
        session,
        channel,
        project,
        headline=payload.get("headline"),
        eyebrow=payload.get("eyebrow"),
        background_asset_id=_uuid(payload.get("background_asset_id")),
        concept=payload.get("concept"),
        user_id=_uuid(payload.get("user_id")),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
    )
    for warning in result.warnings:
        job_queue.log(session, job, warning, level="WARNING")
    return result.to_dict()
