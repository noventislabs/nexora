"""Content project lifecycle.

A project is the unit that travels the pipeline. It is created from an *approved*
topic candidate that has been researched — never from a bare idea, because a script
with nothing behind it is exactly what this system refuses to produce.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, NotFound, ValidationError
from nexora.db.models import (
    Channel,
    ContentProject,
    ContentScript,
    ScriptVersion,
    TopicCandidate,
    TopicResearch,
)
from nexora.db.models.enums import ActorType, CandidateStatus, ProjectStatus, VideoFormat
from nexora.services import audit
from nexora.services.channels import get_channel_settings

#: Ordered pipeline stages. A project only ever moves forward through these.
STAGE_ORDER = [
    ProjectStatus.DRAFT,
    ProjectStatus.RESEARCHING,
    ProjectStatus.SCRIPTING,
    ProjectStatus.FACT_CHECK,
    ProjectStatus.VOICE,
    ProjectStatus.ASSETS,
    ProjectStatus.RENDERING,
    ProjectStatus.READY,
    ProjectStatus.SCHEDULED,
    ProjectStatus.PUBLISHING,
    ProjectStatus.PUBLISHED,
]


def create_from_candidate(
    session: Session,
    channel: Channel,
    candidate: TopicCandidate,
    *,
    video_format: str | None = None,
    target_duration_seconds: int | None = None,
    language: str | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> ContentProject:
    if candidate.channel_id != channel.id:
        raise NotFound("Topic candidate not found for this channel.")
    if candidate.status == CandidateStatus.CONVERTED.value:
        raise Conflict("This candidate has already been turned into a content project.")
    if candidate.status not in (CandidateStatus.APPROVED.value, CandidateStatus.SAVED.value):
        raise ValidationError(
            "Only an approved or saved candidate can become a content project. "
            f"This one is '{candidate.status}'."
        )

    research = session.execute(
        select(TopicResearch).where(
            TopicResearch.topic_candidate_id == candidate.id, TopicResearch.status == "SUCCESS"
        )
    ).scalar_one_or_none()
    if research is None:
        raise ValidationError(
            "Research this candidate first. A script is only written against collected evidence."
        )

    settings_row = get_channel_settings(session, channel.id)
    if research.document_count < settings_row.research_min_documents:
        raise ValidationError(
            f"This research stands on {research.document_count} document(s), below the "
            f"channel minimum of {settings_row.research_min_documents}. Collect more sources "
            "or lower the threshold in channel settings."
        )

    resolved_format = video_format or settings_row.default_video_format
    if resolved_format not in {member.value for member in VideoFormat}:
        raise ValidationError(f"Unknown video format '{resolved_format}'.")

    duration = target_duration_seconds or _default_duration(settings_row, resolved_format)
    _validate_duration(duration, resolved_format)

    project = ContentProject(
        channel_id=channel.id,
        topic_candidate_id=candidate.id,
        research_id=research.id,
        title=candidate.title[:300],
        status=ProjectStatus.SCRIPTING.value,
        video_format=resolved_format,
        target_duration_seconds=duration,
        language=language or channel.primary_language,
        approval_status="pending",
    )
    session.add(project)
    session.flush()

    session.add(ContentScript(content_project_id=project.id, current_version=0, status="draft"))
    candidate.status = CandidateStatus.CONVERTED.value
    candidate.decided_at = datetime.now(UTC)
    candidate.decided_by = user_id
    session.flush()

    audit.record(
        session,
        action="project.created",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="content_project",
        entity_id=project.id,
        summary=f"Created project '{project.title[:100]}' from candidate {candidate.id}",
    )
    return project


def _default_duration(settings_row: Any, video_format: str) -> int:
    if video_format == VideoFormat.SHORT.value:
        return 50
    midpoint = (
        settings_row.target_duration_min_seconds + settings_row.target_duration_max_seconds
    ) // 2
    return midpoint


def _validate_duration(duration: int, video_format: str) -> None:
    if video_format == VideoFormat.SHORT.value:
        # YouTube's own limit for Shorts.
        if not 5 <= duration <= 180:
            raise ValidationError("A Short must target between 5 and 180 seconds.")
    elif not 60 <= duration <= 3600:
        raise ValidationError("A long-form video must target between 60 and 3600 seconds.")


def get_project(session: Session, channel_id: uuid.UUID, project_id: uuid.UUID) -> ContentProject:
    project = session.get(ContentProject, project_id)
    if project is None or project.channel_id != channel_id:
        raise NotFound("Content project not found.")
    return project


def advance_status(
    session: Session, project: ContentProject, status: ProjectStatus, *, note: str | None = None
) -> ContentProject:
    """Move a project to a new stage, recording the transition."""
    previous = project.status
    project.status = status.value
    if note:
        project.notes = note
    session.flush()
    audit.record(
        session,
        action="project.status_changed",
        actor_type=ActorType.SYSTEM,
        channel_id=project.channel_id,
        entity_type="content_project",
        entity_id=project.id,
        summary=f"{previous} → {status.value}" + (f": {note}" if note else ""),
        before={"status": previous},
        after={"status": status.value},
    )
    return project


def get_script(session: Session, project: ContentProject) -> ContentScript:
    script = session.execute(
        select(ContentScript).where(ContentScript.content_project_id == project.id)
    ).scalar_one_or_none()
    if script is None:
        script = ContentScript(content_project_id=project.id, current_version=0, status="draft")
        session.add(script)
        session.flush()
    return script


def list_versions(session: Session, script: ContentScript) -> list[ScriptVersion]:
    return list(
        session.execute(
            select(ScriptVersion)
            .where(ScriptVersion.script_id == script.id)
            .order_by(ScriptVersion.version.desc())
        ).scalars()
    )


def current_version(session: Session, project: ContentProject) -> ScriptVersion | None:
    if project.current_script_version_id is None:
        return None
    return session.get(ScriptVersion, project.current_script_version_id)


def project_counts(session: Session, channel_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(ContentProject.status, func.count())
        .where(ContentProject.channel_id == channel_id)
        .group_by(ContentProject.status)
    ).all()
    counts = {member.value: 0 for member in ProjectStatus}
    for status_value, count in rows:
        counts[status_value] = count
    return counts


def project_to_dict(project: ContentProject) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "channel_id": str(project.channel_id),
        "title": project.title,
        "status": project.status,
        "video_format": project.video_format,
        "target_duration_seconds": project.target_duration_seconds,
        "language": project.language,
        "topic_candidate_id": str(project.topic_candidate_id) if project.topic_candidate_id else None,
        "research_id": str(project.research_id) if project.research_id else None,
        "current_script_version_id": (
            str(project.current_script_version_id) if project.current_script_version_id else None
        ),
        "approval_status": project.approval_status,
        "approved_at": project.approved_at.isoformat() if project.approved_at else None,
        "rejection_reason": project.rejection_reason,
        "last_error": project.last_error,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
