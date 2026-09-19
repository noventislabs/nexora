"""Voice, asset, render and thumbnail endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import func, select

from nexora.api.deps import AuthUser, DbSession, RequestContext, Writer
from nexora.api.schemas import ApiModel
from nexora.core.errors import NotFound, ValidationError
from nexora.db.models import VideoAsset, VideoRenderJob, VoiceJob
from nexora.db.models.enums import ActorType, AssetKind
from nexora.queue import jobs as job_queue
from nexora.queue.types import THUMBNAIL_GENERATION, VIDEO_RENDER, VOICE_GENERATION
from nexora.services import assets as asset_service
from nexora.services import channels as channel_service
from nexora.services import projects as project_service
from nexora.services import thumbnails as thumbnail_service
from nexora.services import voice as voice_service
from nexora.services.video import render as render_service

voice_router = APIRouter(prefix="/api/voice", tags=["voice"])
assets_router = APIRouter(prefix="/api/assets", tags=["assets"])
video_router = APIRouter(prefix="/api/video", tags=["video"])
thumbnails_router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])

#: Streaming an asset a chunk at a time keeps a 1080p render off the heap.
STREAM_CHUNK = 1024 * 1024


class GenerateVoiceRequest(ApiModel):
    content_project_id: uuid.UUID
    voice_id: str | None = Field(default=None, max_length=128)
    background: bool = False


class RenderRequest(ApiModel):
    content_project_id: uuid.UUID
    burn_subtitles: bool | None = None
    background: bool = True


class GenerateThumbnailRequest(ApiModel):
    content_project_id: uuid.UUID
    headline: str | None = Field(default=None, max_length=200)
    eyebrow: str | None = Field(default=None, max_length=48)
    concept: str | None = Field(default=None, max_length=1000)
    background_asset_id: uuid.UUID | None = None
    background: bool = False


class ThumbnailDecisionRequest(ApiModel):
    approved: bool


class AssetLicenseRequest(ApiModel):
    license_type: str = Field(max_length=64)
    license_status: str | None = Field(default=None, max_length=32)
    license_url: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)


def _resolve_channel(db, current, channel_id: uuid.UUID | None):
    if channel_id is not None:
        return channel_service.get_channel_for_user(db, current.user, channel_id)
    channel = channel_service.default_channel(db, current.user)
    if channel is None:
        raise NotFound("No channel has been created yet.")
    return channel


# ----------------------------------------------------------------------------- voice
@voice_router.get("/status")
def voice_status(current: AuthUser) -> dict[str, Any]:
    """Voice provider availability, voices and quota where the provider exposes it."""
    return voice_service.voice_status()


@voice_router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_voice(
    payload: GenerateVoiceRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Synthesize narration.

    Returns 503 ``provider_not_configured`` with no voice provider. No silent track,
    no placeholder audio.
    """
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            VOICE_GENERATION,
            channel_id=channel.id,
            content_project_id=project.id,
            queue="media",
            payload={
                "voice_id": payload.voice_id,
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    # Surface NOT_CONFIGURED before the workflow check, for the same reason.
    voice_service.require_provider()
    version = project_service.current_version(db, project)
    if version is None:
        raise ValidationError("This project has no current script version to narrate.")
    result = voice_service.generate_narration(
        db, channel, project, version, voice_id=payload.voice_id, user_id=current.user.id
    )
    return {"mode": "inline", **result.to_dict()}


@voice_router.get("/{project_id}/jobs")
def list_voice_jobs(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    rows = list(
        db.execute(
            select(VoiceJob)
            .where(VoiceJob.content_project_id == project.id)
            .order_by(VoiceJob.created_at.desc())
        ).scalars()
    )
    return {"items": [voice_service.job_to_dict(row) for row in rows], "total": len(rows)}


# ---------------------------------------------------------------------------- assets
@assets_router.get("")
def list_assets(
    db: DbSession,
    current: AuthUser,
    channel_id: uuid.UUID | None = None,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    content_project_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    conditions = [VideoAsset.channel_id == channel.id]
    if kind:
        conditions.append(VideoAsset.kind == kind)
    if content_project_id:
        conditions.append(VideoAsset.content_project_id == content_project_id)

    total = db.execute(select(func.count()).select_from(VideoAsset).where(*conditions)).scalar_one()
    rows = asset_service.list_assets(
        db, channel.id, kind=kind, content_project_id=content_project_id, limit=limit, offset=offset
    )
    return {
        "items": [asset_service.asset_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "kinds": [member.value for member in AssetKind],
        "license_types": sorted(asset_service.LICENSE_KINDS),
    }


@assets_router.post("", status_code=status.HTTP_201_CREATED)
async def upload_asset(
    db: DbSession,
    current: Writer,
    ctx: RequestContext,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form(max_length=32)],
    license_type: Annotated[str, Form(max_length=64)] = "unknown",
    license_url: Annotated[str | None, Form(max_length=2000)] = None,
    attribution: Annotated[str | None, Form(max_length=500)] = None,
    source_url: Annotated[str | None, Form(max_length=2000)] = None,
    note: Annotated[str | None, Form(max_length=2000)] = None,
    content_project_id: Annotated[uuid.UUID | None, Form()] = None,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Upload an asset. Content is validated by magic bytes, not by filename."""
    channel = _resolve_channel(db, current, channel_id)

    data = await file.read(asset_service.MAX_UPLOAD_BYTES + 1)
    if len(data) > asset_service.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File exceeds the {asset_service.MAX_UPLOAD_BYTES // 1_048_576} MB limit."
        )

    asset = asset_service.store_bytes(
        db,
        channel,
        kind=kind,
        data=data,
        content_project_id=content_project_id,
        source="upload",
        source_url=source_url,
        license_type=license_type,
        license_url=license_url,
        attribution=attribution,
        usage_permission_note=note,
        user_id=current.user.id,
    )
    return asset_service.asset_to_dict(asset)


@assets_router.patch("/{asset_id}/license")
def update_license(
    asset_id: uuid.UUID,
    payload: AssetLicenseRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    asset = asset_service.get_asset(db, channel.id, asset_id)
    asset_service.set_license(
        db,
        asset,
        license_type=payload.license_type,
        license_status=payload.license_status,
        license_url=payload.license_url,
        attribution=payload.attribution,
        note=payload.note,
        user_id=current.user.id,
    )
    return asset_service.asset_to_dict(asset)


@assets_router.get("/{asset_id}/content")
def download_asset(
    asset_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> StreamingResponse:
    channel = _resolve_channel(db, current, channel_id)
    asset = asset_service.get_asset(db, channel.id, asset_id)
    return StreamingResponse(
        asset_service.open_asset(asset),
        media_type=asset.mime_type or "application/octet-stream",
        headers={
            # Attachment + nosniff: an uploaded SVG or HTML must never execute on our origin.
            "content-disposition": f'attachment; filename="{asset.id}"',
            "x-content-type-options": "nosniff",
            "cache-control": "private, max-age=3600",
        },
    )


# ----------------------------------------------------------------------------- video
@video_router.get("/capability")
def render_capability(current: AuthUser) -> dict[str, Any]:
    """Whether a render can actually be produced, from a real FFmpeg probe."""
    return render_service.render_availability()


@video_router.post("/render", status_code=status.HTTP_201_CREATED)
def render(
    payload: RenderRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            VIDEO_RENDER,
            channel_id=channel.id,
            content_project_id=project.id,
            queue="media",
            payload={
                "burn_subtitles": payload.burn_subtitles,
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = render_service.render_project(
        db, channel, project, burn_subtitles=payload.burn_subtitles, user_id=current.user.id
    )
    return {"mode": "inline", **result.to_dict()}


@video_router.get("/{project_id}/renders")
def list_renders(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    from nexora.db.models import VideoProject

    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    video_project = db.execute(
        select(VideoProject).where(VideoProject.content_project_id == project.id)
    ).scalar_one_or_none()
    if video_project is None:
        return {"items": [], "total": 0, "scene_plan": []}

    rows = list(
        db.execute(
            select(VideoRenderJob)
            .where(VideoRenderJob.video_project_id == video_project.id)
            .order_by(VideoRenderJob.created_at.desc())
        ).scalars()
    )
    return {
        "items": [render_service.job_to_dict(row) for row in rows],
        "total": len(rows),
        "scene_plan": video_project.scene_plan or [],
        "aspect_ratio": video_project.aspect_ratio,
        "resolution": video_project.resolution,
        "subtitle_burn_in": video_project.subtitle_burn_in,
    }


# ------------------------------------------------------------------------ thumbnails
@thumbnails_router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_thumbnail(
    payload: GenerateThumbnailRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, payload.content_project_id)

    if payload.background:
        job = job_queue.enqueue(
            db,
            THUMBNAIL_GENERATION,
            channel_id=channel.id,
            content_project_id=project.id,
            queue="media",
            payload={
                "headline": payload.headline,
                "eyebrow": payload.eyebrow,
                "concept": payload.concept,
                "background_asset_id": (
                    str(payload.background_asset_id) if payload.background_asset_id else None
                ),
                "actor_type": ActorType.USER.value,
                "user_id": str(current.user.id),
            },
        )
        db.flush()
        job_queue.signal(job)
        return {"mode": "queued", "job_id": str(job.id), "status": job.status}

    result = thumbnail_service.generate(
        db,
        channel,
        project,
        headline=payload.headline,
        eyebrow=payload.eyebrow,
        background_asset_id=payload.background_asset_id,
        concept=payload.concept,
        user_id=current.user.id,
    )
    return {"mode": "inline", **result.to_dict()}


@thumbnails_router.get("/{project_id}")
def list_thumbnails(
    project_id: uuid.UUID, db: DbSession, current: AuthUser, channel_id: uuid.UUID | None = None
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    rows = thumbnail_service.list_for_project(db, project.id)
    items = []
    for row in rows:
        asset = db.get(VideoAsset, row.asset_id) if row.asset_id else None
        items.append(thumbnail_service.thumbnail_to_dict(row, asset))
    return {
        "items": items,
        "total": len(items),
        "current_thumbnail_id": (
            str(project.current_thumbnail_id) if project.current_thumbnail_id else None
        ),
    }


@thumbnails_router.post("/{project_id}/{thumbnail_id}/decision")
def decide_thumbnail(
    project_id: uuid.UUID,
    thumbnail_id: uuid.UUID,
    payload: ThumbnailDecisionRequest,
    db: DbSession,
    current: Writer,
    channel_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    channel = _resolve_channel(db, current, channel_id)
    project = project_service.get_project(db, channel.id, project_id)
    thumbnail = thumbnail_service.get_thumbnail(db, project.id, thumbnail_id)
    thumbnail_service.decide(db, thumbnail, approved=payload.approved, user_id=current.user.id)
    asset = db.get(VideoAsset, thumbnail.asset_id) if thumbnail.asset_id else None
    return thumbnail_service.thumbnail_to_dict(thumbnail, asset)
