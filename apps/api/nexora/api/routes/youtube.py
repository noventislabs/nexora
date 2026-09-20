"""YouTube connection and publishing endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import func, select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.config import settings
from nexora.core.errors import NotFound, ValidationError
from nexora.db.models import ContentProject, PublishJob, YouTubeVideo
from nexora.db.models.enums import ActorType, PublishAuthorization
from nexora.queue import jobs as job_queue
from nexora.queue.types import METADATA_GENERATION, YOUTUBE_UPLOAD
from nexora.services import channels as channel_service
from nexora.services import metadata as metadata_service
from nexora.services import projects as project_service
from nexora.services import publishing as publishing_service
from nexora.services import quality as quality_service
from nexora.services.youtube import oauth as oauth_service
from nexora.services.youtube import public as public_service

youtube_router = APIRouter(prefix="/api/youtube", tags=["youtube"])
publish_router = APIRouter(prefix="/api/publish", tags=["publishing"])
metadata_router = APIRouter(prefix="/api/metadata", tags=["metadata"])


class ConnectRequest(ApiModel):
    include_analytics: bool = True
    include_monetary: bool = False
    redirect_to: str | None = Field(default=None, max_length=512)


class LinkPublicChannelRequest(ApiModel):
    channel_identifier: str = Field(max_length=200)


class GenerateMetadataRequest(ApiModel):
    content_project_id: uuid.UUID
    background: bool = False


class PublishRequest(ApiModel):
    content_project_id: uuid.UUID
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    scheduled_for: datetime | None = None
    background: bool = True
    #: An operator override of a blocked preflight. Autopilot may never set this.
    force: bool = False


class ApprovalRequest(ApiModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=1000)


def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


# ------------------------------------------------------------------- connection
@youtube_router.get("/connection")
def get_connection(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Connection state. No token material is ever returned."""
    from nexora.services.availability import (
        youtube_data_api_availability,
        youtube_oauth_availability,
    )

    channel = _resolve_channel(db, current, channel_id)
    try:
        connection = oauth_service.get_connection(db, channel.id)
        payload = oauth_service.connection_to_dict(connection)
    except NotFound:
        payload = {"status": "not_connected", "connected": False, "capabilities": {}}

    return {
        **payload,
        "oauth_app": youtube_oauth_availability().to_dict(),
        "data_api_key": youtube_data_api_availability().to_dict(),
        "note": (
            "A channel id identifies a channel but grants nothing. Uploading requires "
            "OAuth consent from the channel owner."
        ),
    }


@youtube_router.post("/connect")
def start_connect(
    payload: ConnectRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Begin the OAuth flow and return the Google consent URL."""
    channel = _resolve_channel(db, current, channel_id)
    request = oauth_service.start_authorization(
        db,
        current.user,
        channel,
        include_analytics=payload.include_analytics,
        include_monetary=payload.include_monetary,
        redirect_to=payload.redirect_to,
    )
    return {
        "authorization_url": request.authorization_url,
        "expires_at": request.expires_at.isoformat(),
        "note": "Sign in with Google. NEXORA never sees your password.",
    }


@youtube_router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    db: DbSession,
    state: Annotated[str, Query(max_length=256)] = "",
    code: Annotated[str, Query(max_length=2048)] = "",
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    """Google's redirect target.

    Unauthenticated by design — Google calls it, not the browser session. The single-use
    ``state`` is what binds the response to the request that started it.
    """
    base = settings.app_base_url.rstrip("/")
    if error:
        return RedirectResponse(f"{base}/dashboard/channels?youtube_error={error}", status_code=303)
    if not state or not code:
        return RedirectResponse(
            f"{base}/dashboard/channels?youtube_error=missing_parameters", status_code=303
        )

    try:
        channel, connection = oauth_service.complete_authorization(db, state=state, code=code)
    except Exception as exc:
        from nexora.core.errors import NexoraError

        reason = exc.code if isinstance(exc, NexoraError) else "connection_failed"
        return RedirectResponse(f"{base}/dashboard/channels?youtube_error={reason}", status_code=303)

    return RedirectResponse(
        f"{base}/dashboard/channels?youtube_connected={connection.youtube_channel_id}",
        status_code=303,
    )


@youtube_router.post("/disconnect")
def disconnect(
    db: DbSession, current: Writer, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    connection = oauth_service.disconnect(db, channel, user_id=current.user.id)
    return oauth_service.connection_to_dict(connection)


@youtube_router.post("/public-channel")
def link_public_channel(
    payload: LinkPublicChannelRequest,
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Verify and link a channel id using the public Data API.

    This identifies the channel and enables public statistics. It grants no upload
    access, and it never marks the OAuth connection as connected.
    """
    channel = _resolve_channel(db, current, channel_id)
    connection, public = public_service.link_public_channel(
        db, channel, payload.channel_identifier, user_id=current.user.id
    )
    return {
        "channel": public.to_dict(),
        "connection": oauth_service.connection_to_dict(connection),
    }


@youtube_router.get("/public-channel")
def get_public_channel(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    return public_service.public_status(db, channel)


@youtube_router.post("/public-channel/refresh")
def refresh_public_channel(
    db: DbSession, current: Writer, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Re-read the public statistics and store a fresh snapshot."""
    channel = _resolve_channel(db, current, channel_id)
    connection = oauth_service.get_connection(db, channel.id)
    if not connection.public_channel_id:
        raise ValidationError("No public channel is linked to this NEXORA channel.")

    public = public_service.fetch_public_channel(connection.public_channel_id)
    snapshot = public_service.record_public_snapshot(db, channel, public)
    return {
        "channel": public.to_dict(),
        "snapshot_id": str(snapshot.id),
        "captured_at": snapshot.captured_at.isoformat(),
    }


@youtube_router.get("/videos")
def list_videos(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    condition = YouTubeVideo.channel_id == channel.id
    total = db.execute(select(func.count()).select_from(YouTubeVideo).where(condition)).scalar_one()
    rows = list(
        db.execute(
            select(YouTubeVideo)
            .where(condition)
            .order_by(YouTubeVideo.published_at.desc().nullslast())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [publishing_service.video_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------- metadata
@metadata_router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_metadata(
    payload: GenerateMetadataRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            METADATA_GENERATION,
            channel_id=channel.id,
            content_project_id=project.id,
            payload={"actor_type": ActorType.USER.value, "user_id": str(current.user.id)},
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = metadata_service.generate_metadata(db, channel, project, user_id=current.user.id)
    return {
        "mode": "inline",
        **metadata_service.metadata_to_dict(result.version),
        "warnings": result.warnings,
    }


@metadata_router.get("/{project_id}")
def get_metadata(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    record = metadata_service.latest_metadata(db, project.id)
    if record is None:
        raise NotFound("No metadata has been generated for this project.")
    return metadata_service.metadata_to_dict(record)


# -------------------------------------------------------------------- publishing
@publish_router.get("/preflight/{project_id}")
def preflight(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Every publishing gate and its current state. Nothing uploads from here."""
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    result = publishing_service.run_preflight(
        db, channel, project, authorized_by=PublishAuthorization.USER
    )
    quality = quality_service.latest_quality_check(db, project.id)
    copyright_check = quality_service.latest_copyright_check(db, project.id)
    return {
        **result.to_dict(),
        "quality": quality_service.quality_to_dict(quality) if quality else None,
        "copyright": quality_service.copyright_to_dict(copyright_check) if copyright_check else None,
    }


@publish_router.post("/approve/{project_id}")
def approve(
    project_id: uuid.UUID,
    payload: ApprovalRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Record the human approval decision that autonomous publishing requires."""
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    publishing_service.approve_project(
        db, project, approved=payload.approved, user_id=current.user.id, reason=payload.reason
    )
    return project_service.project_to_dict(project)


@publish_router.post("", status_code=status.HTTP_201_CREATED)
def publish(
    payload: PublishRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Authorize a publish and queue the upload.

    Returns 409 ``safety_blocked`` when a gate fails, naming every blocker.
    """
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    job = publishing_service.create_publish_job(
        db,
        channel,
        project,
        authorized_by=PublishAuthorization.USER,
        privacy_status=payload.privacy_status,
        scheduled_for=payload.scheduled_for,
        user_id=current.user.id,
        force=payload.force,
    )

    if payload.background:
        queued = job_queue.enqueue(
            db,
            YOUTUBE_UPLOAD,
            channel_id=channel.id,
            content_project_id=project.id,
            queue="media",
            # The publish job's own key: re-queuing cannot create a second upload.
            idempotency_key=f"upload:{job.idempotency_key}",
            payload={"publish_job_id": str(job.id)},
            available_at=payload.scheduled_for,
        )
        job.job_id = queued.id
        db.flush()
        if payload.scheduled_for is None:
            job_queue.signal(queued)
        return {
            "mode": "queued",
            "publish_job": publishing_service.job_to_dict(job),
            "job_id": str(queued.id),
        }

    publishing_service.execute_publish(db, job)
    return {"mode": "inline", "publish_job": publishing_service.job_to_dict(job)}


@publish_router.get("")
def list_publish_jobs(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    condition = PublishJob.channel_id == channel.id
    total = db.execute(select(func.count()).select_from(PublishJob).where(condition)).scalar_one()
    rows = list(
        db.execute(
            select(PublishJob)
            .where(condition)
            .order_by(PublishJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return {
        "items": [publishing_service.job_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@publish_router.get("/{job_id}")
def get_publish_job(
    job_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    job = publishing_service.get_publish_job(db, channel.id, job_id)
    return publishing_service.job_to_dict(job)


@publish_router.get("/pending/approvals")
def pending_approvals(
    db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    rows = list(
        db.execute(
            select(ContentProject)
            .where(
                ContentProject.channel_id == channel.id,
                ContentProject.approval_status == "pending",
                ContentProject.status.in_(["ready", "scheduled"]),
            )
            .order_by(ContentProject.updated_at.desc())
        ).scalars()
    )
    return {
        "items": [project_service.project_to_dict(row) for row in rows],
        "total": len(rows),
    }
