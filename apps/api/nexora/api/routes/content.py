"""Research, content project, script and fact-check endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import func, select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.core.errors import NotFound, ValidationError
from nexora.db.models import (
    ContentProject,
    ResearchDocument,
    TopicCandidate,
    TopicResearch,
)
from nexora.db.models.enums import ActorType, CheckStatus, ProjectStatus
from nexora.queue import jobs as job_queue
from nexora.queue.types import FACT_CHECK, RESEARCH, SCRIPT_GENERATION
from nexora.services import audit
from nexora.services import channels as channel_service
from nexora.services import factcheck as factcheck_service
from nexora.services import projects as project_service
from nexora.services import scripts as script_service
from nexora.services.research.engine import get_research, run_research

research_router = APIRouter(prefix="/api/research", tags=["research"])
projects_router = APIRouter(prefix="/api/content", tags=["content"])
scripts_router = APIRouter(prefix="/api/scripts", tags=["scripts"])
factcheck_router = APIRouter(prefix="/api/fact-check", tags=["fact-check"])


# --------------------------------------------------------------------------- schemas
class RunResearchRequest(ApiModel):
    topic_candidate_id: uuid.UUID
    force: bool = False
    background: bool = False


class CreateProjectRequest(ApiModel):
    topic_candidate_id: uuid.UUID
    video_format: Literal["long_form", "short"] | None = None
    target_duration_seconds: int | None = Field(default=None, ge=5, le=3600)
    language: str | None = Field(default=None, max_length=16)


class GenerateScriptRequest(ApiModel):
    content_project_id: uuid.UUID
    guidance: str | None = Field(default=None, max_length=2000)
    background: bool = False


class SelectVersionRequest(ApiModel):
    script_version_id: uuid.UUID


class RunFactCheckRequest(ApiModel):
    content_project_id: uuid.UUID
    script_version_id: uuid.UUID | None = None
    background: bool = False


# ------------------------------------------------------------------------ helpers
def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


def _document_dict(document: ResearchDocument, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "id": str(document.id),
        "origin": document.origin,
        "title": document.title,
        "url": document.url,
        "publisher": document.publisher,
        "author": document.author,
        "published_at": document.published_at.isoformat() if document.published_at else None,
        "fetched_at": document.fetched_at.isoformat() if document.fetched_at else None,
        "fetch_decision": document.fetch_decision,
        "fetch_note": document.fetch_note,
        "http_status": document.http_status,
        "word_count": document.word_count,
        "truncated": document.truncated,
        "has_text": bool(document.text),
        "error": document.error,
    }


def _research_dict(
    research: TopicResearch, documents: list[ResearchDocument] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(research.id),
        "topic_candidate_id": str(research.topic_candidate_id),
        "status": research.status,
        "summary": research.summary,
        "key_facts": research.key_facts or [],
        "claims": research.claims or [],
        "statistics": research.statistics or [],
        "entities": research.entities or {},
        "conflicts": research.conflicts or [],
        "uncertainties": research.uncertainties or [],
        "sources": research.sources or [],
        "document_count": research.document_count,
        "provider": research.provider,
        "model": research.model,
        "error": research.error,
        "started_at": research.started_at.isoformat() if research.started_at else None,
        "finished_at": research.finished_at.isoformat() if research.finished_at else None,
        "created_at": research.created_at.isoformat() if research.created_at else None,
    }
    if documents is not None:
        payload["documents"] = [
            _document_dict(document, index) for index, document in enumerate(documents)
        ]
    return payload


def _documents_for(db, research: TopicResearch) -> list[ResearchDocument]:
    return list(
        db.execute(
            select(ResearchDocument)
            .where(ResearchDocument.research_id == research.id)
            .order_by(ResearchDocument.created_at.asc())
        ).scalars()
    )


# -------------------------------------------------------------------------- research
@research_router.get("")
def list_research(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    condition = TopicResearch.channel_id == channel.id
    total = db.execute(select(func.count()).select_from(TopicResearch).where(condition)).scalar_one()
    rows = list(
        db.execute(
            select(TopicResearch)
            .where(condition)
            .order_by(TopicResearch.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [_research_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "channel_id": str(channel.id),
    }


@research_router.post("", status_code=status.HTTP_201_CREATED)
def create_research(
    payload: RunResearchRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Research a topic candidate.

    Returns 503 ``provider_not_configured`` with no LLM provider. Nothing is invented.
    """
    channel = _resolve_channel(db, current, channel_id)
    candidate = db.get(TopicCandidate, payload.topic_candidate_id)
    if candidate is None or candidate.channel_id != channel.id:
        raise NotFound("Topic candidate not found.")

    if payload.background:
        job = job_queue.enqueue(
            db,
            RESEARCH,
            channel_id=channel.id,
            payload={
                "topic_candidate_id": str(candidate.id),
                "force": payload.force,
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = run_research(
        db,
        channel,
        candidate,
        actor_type=ActorType.USER,
        user_id=current.user.id,
        force=payload.force,
    )
    audit.record(
        db,
        action="research.requested",
        user_id=current.user.id,
        channel_id=channel.id,
        entity_type="topic_research",
        entity_id=result.research.id,
        **ctx,
    )
    return {
        "mode": "inline",
        **_research_dict(result.research, result.documents),
        "dropped": result.dropped,
    }


@research_router.get("/{research_id}")
def get_research_detail(
    research_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    research = get_research(db, channel.id, research_id)
    return _research_dict(research, _documents_for(db, research))


# -------------------------------------------------------------------------- projects
@projects_router.get("")
def list_projects(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    conditions = [ContentProject.channel_id == channel.id]
    if status_filter:
        conditions.append(ContentProject.status == status_filter)

    total = db.execute(
        select(func.count()).select_from(ContentProject).where(*conditions)
    ).scalar_one()
    rows = list(
        db.execute(
            select(ContentProject)
            .where(*conditions)
            .order_by(ContentProject.updated_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [project_service.project_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "counts": project_service.project_counts(db, channel.id),
        "channel_id": str(channel.id),
    }


@projects_router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: CreateProjectRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    candidate = db.get(TopicCandidate, payload.topic_candidate_id)
    if candidate is None or candidate.channel_id != channel.id:
        raise NotFound("Topic candidate not found.")

    project = project_service.create_from_candidate(
        db,
        channel,
        candidate,
        video_format=payload.video_format,
        target_duration_seconds=payload.target_duration_seconds,
        language=payload.language,
        user_id=current.user.id,
    )
    return project_service.project_to_dict(project)


@projects_router.get("/{project_id}")
def get_project_detail(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    script = project_service.get_script(db, project)
    versions = project_service.list_versions(db, script)
    current_version = project_service.current_version(db, project)
    check = factcheck_service.latest_for_project(db, project.id)

    research_payload = None
    if project.research_id is not None:
        research = db.get(TopicResearch, project.research_id)
        if research is not None:
            research_payload = _research_dict(research, _documents_for(db, research))

    return {
        **project_service.project_to_dict(project),
        "research": research_payload,
        "script": {
            "current_version": script.current_version,
            "status": script.status,
            "versions": [
                script_service.version_to_dict(version, include_text=False) for version in versions
            ],
            "current": (
                script_service.version_to_dict(current_version) if current_version else None
            ),
        },
        "fact_check": factcheck_service.check_to_dict(check) if check else None,
    }


# --------------------------------------------------------------------------- scripts
@scripts_router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_script_endpoint(
    payload: GenerateScriptRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            SCRIPT_GENERATION,
            channel_id=channel.id,
            content_project_id=project.id,
            payload={
                "guidance": payload.guidance,
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = script_service.generate_script(
        db, channel, project, guidance=payload.guidance, user_id=current.user.id
    )
    return {
        "mode": "inline",
        "version": script_service.version_to_dict(result.version),
        "dropped": result.dropped,
        "originality": result.originality,
    }


@scripts_router.get("/{project_id}/versions")
def list_script_versions(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    script = project_service.get_script(db, project)
    versions = project_service.list_versions(db, script)
    return {
        "items": [
            script_service.version_to_dict(version, include_text=False) for version in versions
        ],
        "total": len(versions),
        "current_script_version_id": (
            str(project.current_script_version_id) if project.current_script_version_id else None
        ),
    }


@scripts_router.get("/versions/{version_id}")
def get_script_version(
    version_id: uuid.UUID,
    db: DbSession,
    current: AuthUser,
    content_project_id: uuid.UUID,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, content_project_id)
    version = script_service.get_version(db, project, version_id)
    return script_service.version_to_dict(version)


@scripts_router.post("/{project_id}/select-version")
def select_version(
    project_id: uuid.UUID,
    payload: SelectVersionRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    version = script_service.get_version(db, project, payload.script_version_id)
    script_service.set_current_version(db, project, version, user_id=current.user.id)
    return project_service.project_to_dict(project)


# ------------------------------------------------------------------------ fact check
@factcheck_router.post("", status_code=status.HTTP_201_CREATED)
def run_fact_check_endpoint(
    payload: RunFactCheckRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            FACT_CHECK,
            channel_id=channel.id,
            content_project_id=project.id,
            payload={"actor_type": ActorType.USER.value, "user_id": str(current.user.id)},
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    version = (
        script_service.get_version(db, project, payload.script_version_id)
        if payload.script_version_id
        else project_service.current_version(db, project)
    )
    if version is None:
        raise ValidationError("This project has no script version to fact check.")

    result = factcheck_service.run_fact_check(
        db, channel, project, version, actor_type=ActorType.USER, user_id=current.user.id
    )
    if result.check.status == CheckStatus.PASS.value and project.status == ProjectStatus.FACT_CHECK.value:
        project_service.advance_status(db, project, ProjectStatus.VOICE, note="Fact check passed.")
    return {"mode": "inline", **factcheck_service.check_to_dict(result.check)}


@factcheck_router.get("/{project_id}")
def get_fact_check(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    check = factcheck_service.latest_for_project(db, project.id)
    if check is None:
        raise NotFound("This project has not been fact checked yet.")
    return factcheck_service.check_to_dict(check)
