"""Background handlers for metadata, quality checks and YouTube upload."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound
from nexora.db.models import Channel, ContentProject, Job, PublishJob
from nexora.db.models.enums import ActorType
from nexora.queue import jobs as job_queue
from nexora.queue.types import (
    METADATA_GENERATION,
    QUALITY_CHECK,
    YOUTUBE_UPLOAD,
    register_handler,
)
from nexora.services import metadata as metadata_service
from nexora.services import publishing as publishing_service
from nexora.services import quality as quality_service


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


@register_handler(METADATA_GENERATION)
def handle_metadata_generation(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)
    payload = job.payload or {}

    job_queue.log(session, job, f"Generating metadata for '{project.title[:80]}'.")
    result = metadata_service.generate_metadata(
        session,
        channel,
        project,
        user_id=_uuid(payload.get("user_id")),
        actor_type=ActorType(payload.get("actor_type", ActorType.SYSTEM.value)),
    )
    for warning in result.warnings:
        job_queue.log(session, job, warning, level="WARNING")
    return result.to_dict()


@register_handler(QUALITY_CHECK)
def handle_quality_check(session: Session, job: Job) -> dict[str, Any]:
    channel = _channel(session, job)
    project = _project(session, job)

    job_queue.log(session, job, "Running quality and copyright checks.")
    quality = quality_service.run_quality_check(session, channel, project)
    copyright_check = quality_service.run_copyright_check(session, channel, project)

    for check in quality.checks or []:
        if not check["passed"]:
            job_queue.log(
                session,
                job,
                f"{check['label']}: {check['detail']}",
                level="ERROR" if check["blocking"] else "WARNING",
            )
    return {
        "quality": quality_service.quality_to_dict(quality),
        "copyright": quality_service.copyright_to_dict(copyright_check),
    }


@register_handler(YOUTUBE_UPLOAD)
def handle_youtube_upload(session: Session, job: Job) -> dict[str, Any]:
    payload = job.payload or {}
    publish_job_id = _uuid(payload.get("publish_job_id"))
    if publish_job_id is None:
        raise NotFound("This upload job has no publish job to execute.")

    publish_job = session.get(PublishJob, publish_job_id)
    if publish_job is None:
        raise NotFound(f"Publish job {publish_job_id} no longer exists.")

    if publish_job.youtube_video_id:
        # The idempotency guarantee, enforced at the worker boundary too.
        job_queue.log(
            session,
            job,
            f"Already published as {publish_job.youtube_video_id}; not uploading again.",
        )
        return publishing_service.job_to_dict(publish_job)

    job_queue.log(
        session,
        job,
        f"Uploading {publish_job.upload_bytes or 'unknown'} bytes as "
        f"{publish_job.privacy_status}.",
    )
    publishing_service.execute_publish(session, publish_job)
    job_queue.log(
        session,
        job,
        f"Published and verified: https://www.youtube.com/watch?v={publish_job.youtube_video_id}",
    )
    return publishing_service.job_to_dict(publish_job)
