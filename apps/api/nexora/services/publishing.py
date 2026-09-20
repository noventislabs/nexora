"""Publishing pipeline.

    READY → QUALITY → COPYRIGHT → METADATA → AUTHORIZATION → UPLOAD → VERIFY → RECORD

Every gate is evaluated before a publish job is created, and the result is stored on
the job, so a blocked publish always names its cause.

Idempotency: each project + script version + render gets one deterministic key. A
second publish attempt returns the original job rather than uploading again, and the
worker refuses to start a job that already carries a ``youtube_video_id``. A video is
never uploaded twice.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexora.core.errors import (
    Conflict,
    NotFound,
    ProviderUnavailable,
    SafetyBlocked,
    UpstreamPermanentError,
    ValidationError,
)
from nexora.core.logging import get_logger
from nexora.db.models import (
    Channel,
    ContentProject,
    PublishJob,
    Thumbnail,
    VideoAsset,
    YouTubeVideo,
)
from nexora.db.models.enums import (
    ActorType,
    CheckStatus,
    ProjectStatus,
    PublishAuthorization,
    RunStatus,
)
from nexora.services import assets as asset_service
from nexora.services import audit
from nexora.services import metadata as metadata_service
from nexora.services import quality as quality_service
from nexora.services.youtube import oauth as oauth_service

logger = get_logger(__name__)

VALID_PRIVACY = ("private", "unlisted", "public")
#: Bounded retries for a transient upload failure.
MAX_UPLOAD_ATTEMPTS = 3
BACKOFF_SECONDS = (60, 300, 900)


@dataclass
class Preflight:
    """Every gate, and whether publishing may proceed."""

    gates: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        return not self.blockers

    def add(self, key: str, label: str, passed: bool, detail: str, *, blocking: bool = True) -> None:
        self.gates.append(
            {"key": key, "label": label, "passed": passed, "blocking": blocking, "detail": detail}
        )
        if not passed:
            (self.blockers if blocking else self.warnings).append(f"{label}: {detail}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_publish": self.can_publish,
            "gates": self.gates,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }


def idempotency_key(project: ContentProject, render_asset_id: uuid.UUID) -> str:
    """One key per (project, script version, render). Re-publishing the same artefacts
    returns the existing job instead of uploading a duplicate."""
    basis = f"{project.id}|{project.current_script_version_id}|{render_asset_id}"
    return hashlib.sha256(basis.encode()).hexdigest()


def run_preflight(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    authorized_by: PublishAuthorization,
    privacy_status: str = "private",
    scheduled_for: datetime | None = None,
) -> Preflight:
    """Evaluate every publishing gate. Nothing uploads until this passes."""
    from nexora.services.channels import get_automation_settings

    automation = get_automation_settings(session, channel.id)
    preflight = Preflight()

    # --- Kill switch and emergency stop ----------------------------------------
    preflight.add(
        "emergency_stop",
        "Emergency stop",
        not automation.emergency_stop,
        automation.emergency_stop_reason or "Emergency stop is engaged."
        if automation.emergency_stop
        else "Not engaged.",
    )

    if authorized_by is PublishAuthorization.AUTOPILOT:
        preflight.add(
            "autopilot_enabled",
            "Autopilot",
            automation.autopilot_enabled,
            "Autopilot is ON." if automation.autopilot_enabled else "Autopilot is OFF.",
        )
        preflight.add(
            "auto_publish_enabled",
            "Auto-publishing",
            automation.auto_publish_enabled,
            "Auto-publishing is enabled."
            if automation.auto_publish_enabled
            else "Auto-publishing is OFF, so only an operator may publish.",
        )
        preflight.add(
            "human_approval",
            "Human approval",
            not automation.require_human_approval or project.approval_status == "approved",
            "This channel requires human approval and none has been recorded."
            if automation.require_human_approval and project.approval_status != "approved"
            else "Satisfied.",
        )

    # --- Rate limits -------------------------------------------------------------
    day_count, week_count, last_published = _publish_counts(session, channel.id)
    preflight.add(
        "daily_limit",
        "Videos per day",
        day_count < automation.max_videos_per_day,
        f"{day_count} of {automation.max_videos_per_day} published in the last 24 hours.",
    )
    preflight.add(
        "weekly_limit",
        "Videos per week",
        week_count < automation.max_videos_per_week,
        f"{week_count} of {automation.max_videos_per_week} published in the last 7 days.",
    )
    if last_published is not None and automation.min_interval_minutes > 0:
        elapsed = (datetime.now(UTC) - last_published).total_seconds() / 60
        preflight.add(
            "minimum_interval",
            "Minimum interval",
            elapsed >= automation.min_interval_minutes,
            f"{elapsed:.0f} minutes since the last publish; the channel requires "
            f"{automation.min_interval_minutes}.",
        )

    if scheduled_for is not None:
        hour = _local_hour(scheduled_for, automation.timezone)
        within = _within_window(
            hour, automation.publish_window_start_hour, automation.publish_window_end_hour
        )
        preflight.add(
            "publishing_window",
            "Publishing window",
            within,
            f"{hour:02d}:00 {automation.timezone} is "
            f"{'inside' if within else 'outside'} the allowed "
            f"{automation.publish_window_start_hour:02d}:00–"
            f"{automation.publish_window_end_hour:02d}:00 window.",
        )

    # --- Artefacts ---------------------------------------------------------------
    render_asset = (
        session.get(VideoAsset, project.current_render_asset_id)
        if project.current_render_asset_id
        else None
    )
    preflight.add(
        "render",
        "Rendered video",
        render_asset is not None,
        f"{render_asset.size_bytes} bytes ready to upload."
        if render_asset
        else "This project has no rendered video.",
    )

    metadata = metadata_service.latest_metadata(session, project.id)
    preflight.add(
        "metadata",
        "Metadata",
        metadata is not None,
        f"v{metadata.version}: '{metadata.title[:60]}'"
        if metadata
        else "No metadata has been generated for this project.",
    )

    # --- Checks ------------------------------------------------------------------
    quality = quality_service.run_quality_check(session, channel, project)
    preflight.add(
        "quality_check",
        "Quality check",
        quality.status != CheckStatus.FAIL.value,
        f"{quality.status} ({quality.score}/100): "
        + (
            ", ".join(
                check["label"] for check in quality.checks if check["blocking"] and not check["passed"]
            )
            or "no blocking failures"
        ),
    )

    copyright_check = quality_service.run_copyright_check(session, channel, project)
    preflight.add(
        "copyright_check",
        "Copyright check",
        copyright_check.status != CheckStatus.FAIL.value,
        f"{copyright_check.status}, risk {copyright_check.risk_level}: "
        f"{copyright_check.unknown_license_count} asset(s) with an unestablished licence.",
    )

    # --- Connection --------------------------------------------------------------
    try:
        connection = oauth_service.require_connected(session, channel.id)
        preflight.add(
            "youtube_connection",
            "YouTube connection",
            True,
            f"Connected to {connection.youtube_channel_title or connection.youtube_channel_id}.",
        )
    except (ProviderUnavailable, NotFound) as exc:
        preflight.add("youtube_connection", "YouTube connection", False, str(exc))

    if privacy_status not in VALID_PRIVACY:
        preflight.add(
            "privacy_status",
            "Privacy",
            False,
            f"privacy_status must be one of {', '.join(VALID_PRIVACY)}.",
        )

    return preflight


def create_publish_job(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    authorized_by: PublishAuthorization,
    privacy_status: str = "private",
    scheduled_for: datetime | None = None,
    user_id: uuid.UUID | None = None,
    force: bool = False,
) -> PublishJob:
    """Create (or return) the publish job for this project's current artefacts."""
    preflight = run_preflight(
        session,
        channel,
        project,
        authorized_by=authorized_by,
        privacy_status=privacy_status,
        scheduled_for=scheduled_for,
    )
    if not preflight.can_publish and not force:
        raise SafetyBlocked(
            "Publishing is blocked: " + "; ".join(preflight.blockers),
            details=preflight.to_dict(),
        )
    if force and authorized_by is PublishAuthorization.AUTOPILOT:
        # An override is a human decision, never one automation makes for itself.
        raise SafetyBlocked("Autopilot may not override a blocked preflight.")

    if project.current_render_asset_id is None:
        raise ValidationError("This project has no rendered video to publish.")

    key = idempotency_key(project, project.current_render_asset_id)
    existing = session.execute(
        select(PublishJob).where(PublishJob.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.youtube_video_id:
            raise Conflict(
                f"These artefacts were already published as YouTube video "
                f"{existing.youtube_video_id}. Re-render or select a different script "
                "version to publish again."
            )
        return existing

    job = PublishJob(
        channel_id=channel.id,
        content_project_id=project.id,
        idempotency_key=key,
        status=RunStatus.QUEUED.value,
        scheduled_for=scheduled_for,
        privacy_status=privacy_status,
        publish_at_youtube=scheduled_for,
        authorized_by=authorized_by.value,
        approved_by=user_id if authorized_by is PublishAuthorization.USER else None,
        approved_at=datetime.now(UTC) if authorized_by is PublishAuthorization.USER else None,
        max_attempts=MAX_UPLOAD_ATTEMPTS,
        preflight=preflight.to_dict(),
    )
    session.add(job)
    session.flush()

    project.status = ProjectStatus.SCHEDULED.value if scheduled_for else ProjectStatus.PUBLISHING.value
    session.flush()

    audit.record(
        session,
        action="publish.authorized",
        actor_type=(
            ActorType.AUTOPILOT if authorized_by is PublishAuthorization.AUTOPILOT else ActorType.USER
        ),
        user_id=user_id,
        channel_id=channel.id,
        entity_type="publish_job",
        entity_id=job.id,
        summary=(
            f"Publish authorized by {authorized_by.value} as {privacy_status}"
            + (f", scheduled for {scheduled_for.isoformat()}" if scheduled_for else "")
            + (f" (FORCED past: {'; '.join(preflight.blockers)})" if force and preflight.blockers else "")
        ),
        after=preflight.to_dict(),
    )
    return job


def execute_publish(session: Session, job: PublishJob) -> PublishJob:
    """Upload the render to YouTube, then verify the video exists."""
    if job.youtube_video_id:
        # The strongest duplicate guard: never upload twice for one job.
        logger.info(
            "publish.already_uploaded",
            extra={"job_id": str(job.id), "youtube_video_id": job.youtube_video_id},
        )
        return job
    if job.permanent_failure:
        raise UpstreamPermanentError(f"This publish job failed permanently: {job.last_error}")

    channel = session.get(Channel, job.channel_id)
    project = session.get(ContentProject, job.content_project_id)
    if channel is None or project is None:
        raise NotFound("The channel or project for this publish job no longer exists.")

    metadata = metadata_service.latest_metadata(session, project.id)
    if metadata is None:
        raise ValidationError("This project has no metadata to publish with.")
    render_asset = session.get(VideoAsset, project.current_render_asset_id)
    if render_asset is None:
        raise ValidationError("The rendered video for this project is missing from storage.")

    connection = oauth_service.require_connected(session, channel.id)
    token = oauth_service.access_token(session, connection)
    from nexora.services.providers.youtube import get_youtube

    provider = get_youtube()

    job.status = RunStatus.RUNNING.value
    job.attempt_count += 1
    job.started_at = datetime.now(UTC)
    job.upload_bytes = render_asset.size_bytes
    session.flush()

    try:
        media = _open_render(render_asset)
        result = provider.upload_video(
            token,
            media=media,
            media_size=render_asset.size_bytes or 0,
            title=metadata.title,
            description=metadata.description,
            tags=list(metadata.tags or []),
            privacy_status=job.privacy_status,
            category_id=metadata.category_id,
            publish_at=job.publish_at_youtube,
            made_for_kids=metadata.made_for_kids,
            language=metadata.default_language,
        )
    except UpstreamPermanentError as exc:
        _fail(session, job, exc.message, permanent=True)
        raise
    except Exception as exc:
        _fail(session, job, f"{type(exc).__name__}: {exc}", permanent=False)
        raise
    finally:
        try:
            media.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass

    job.youtube_video_id = result.video_id
    session.flush()
    logger.info(
        "publish.uploaded",
        extra={"job_id": str(job.id), "youtube_video_id": result.video_id},
    )

    # Thumbnail is best-effort: a failure here must not lose a successful upload.
    thumbnail_note = _apply_thumbnail(session, provider, token, project, result.video_id)

    remote = provider.get_video(token, result.video_id)
    if remote is None:
        _fail(
            session,
            job,
            "The upload reported success but the video could not be read back from YouTube.",
            permanent=False,
        )
        raise ProviderUnavailable(job.last_error or "Upload verification failed.")

    video = _record_video(session, channel, project, remote)
    job.status = RunStatus.SUCCESS.value
    job.verified_at = datetime.now(UTC)
    job.finished_at = datetime.now(UTC)
    job.last_error = None
    project.status = ProjectStatus.PUBLISHED.value
    session.flush()

    audit.record(
        session,
        action="publish.completed",
        actor_type=(
            ActorType.AUTOPILOT
            if job.authorized_by == PublishAuthorization.AUTOPILOT.value
            else ActorType.USER
        ),
        user_id=job.approved_by,
        channel_id=channel.id,
        entity_type="youtube_video",
        entity_id=video.id,
        summary=(
            f"Published '{remote.title[:80]}' as {remote.privacy_status} "
            f"(https://www.youtube.com/watch?v={remote.id}); verified via the API."
            + (f" {thumbnail_note}" if thumbnail_note else "")
        ),
    )
    return job


def _open_render(asset: VideoAsset):
    """Open the render for streaming upload, without loading it into memory."""
    local = asset_service.local_path_for(asset)
    if local:
        from pathlib import Path

        if Path(local).is_file():
            return Path(local).open("rb")

    import io

    return io.BytesIO(b"".join(asset_service.open_asset(asset)))


def _apply_thumbnail(
    session: Session, provider: Any, token: str, project: ContentProject, video_id: str
) -> str:
    if project.current_thumbnail_id is None:
        return "No approved thumbnail; YouTube generated one."
    thumbnail = session.get(Thumbnail, project.current_thumbnail_id)
    if thumbnail is None or thumbnail.asset_id is None:
        return "The approved thumbnail is missing."
    asset = session.get(VideoAsset, thumbnail.asset_id)
    if asset is None:
        return "The approved thumbnail's file is missing."

    try:
        provider.set_thumbnail(
            token,
            video_id=video_id,
            image=b"".join(asset_service.open_asset(asset)),
            mime_type=asset.mime_type or "image/png",
        )
        return "Custom thumbnail applied."
    except Exception as exc:
        # Thumbnail upload needs a verified channel; failing is not an upload failure.
        logger.warning(
            "publish.thumbnail_failed", extra={"video_id": video_id, "error": str(exc)}
        )
        return f"The custom thumbnail could not be applied: {exc}"


def _record_video(
    session: Session, channel: Channel, project: ContentProject, remote: Any
) -> YouTubeVideo:
    video = session.execute(
        select(YouTubeVideo).where(
            YouTubeVideo.channel_id == channel.id,
            YouTubeVideo.youtube_video_id == remote.id,
        )
    ).scalar_one_or_none()
    if video is None:
        video = YouTubeVideo(channel_id=channel.id, youtube_video_id=remote.id)
        session.add(video)

    video.content_project_id = project.id
    video.title = remote.title
    video.description = remote.description
    video.tags = remote.tags
    video.privacy_status = remote.privacy_status
    video.upload_status = remote.upload_status
    video.published_at = remote.published_at
    video.duration_seconds = remote.duration_seconds
    video.thumbnail_url = remote.thumbnail_url
    video.last_synced_at = datetime.now(UTC)
    session.flush()
    return video


def _fail(session: Session, job: PublishJob, message: str, *, permanent: bool) -> None:
    job.last_error = message[:4000]
    job.finished_at = datetime.now(UTC)
    if permanent or job.attempt_count >= job.max_attempts:
        job.status = RunStatus.FAILED.value
        job.permanent_failure = True
        job.next_attempt_at = None
    else:
        job.status = RunStatus.QUEUED.value
        index = min(job.attempt_count, len(BACKOFF_SECONDS)) - 1
        job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=BACKOFF_SECONDS[index])
        job.finished_at = None
    session.flush()
    logger.warning(
        "publish.failed",
        extra={
            "job_id": str(job.id),
            "attempt": job.attempt_count,
            "permanent": job.permanent_failure,
            "error": message[:200],
        },
    )


def _publish_counts(
    session: Session, channel_id: uuid.UUID
) -> tuple[int, int, datetime | None]:
    now = datetime.now(UTC)
    day = session.execute(
        select(func.count())
        .select_from(PublishJob)
        .where(
            PublishJob.channel_id == channel_id,
            PublishJob.status == RunStatus.SUCCESS.value,
            PublishJob.finished_at >= now - timedelta(days=1),
        )
    ).scalar_one()
    week = session.execute(
        select(func.count())
        .select_from(PublishJob)
        .where(
            PublishJob.channel_id == channel_id,
            PublishJob.status == RunStatus.SUCCESS.value,
            PublishJob.finished_at >= now - timedelta(days=7),
        )
    ).scalar_one()
    last = session.execute(
        select(func.max(PublishJob.finished_at)).where(
            PublishJob.channel_id == channel_id,
            PublishJob.status == RunStatus.SUCCESS.value,
        )
    ).scalar_one()
    return day, week, last


def _local_hour(moment: datetime, timezone: str) -> int:
    import zoneinfo

    try:
        zone = zoneinfo.ZoneInfo(timezone)
    except Exception:
        zone = UTC
    return moment.astimezone(zone).hour


def _within_window(hour: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour < end
    # A window that wraps past midnight.
    return hour >= start or hour < end


def approve_project(
    session: Session, project: ContentProject, *, approved: bool, user_id: uuid.UUID, reason: str | None = None
) -> ContentProject:
    project.approval_status = "approved" if approved else "rejected"
    project.approved_by = user_id if approved else None
    project.approved_at = datetime.now(UTC) if approved else None
    project.rejection_reason = None if approved else reason
    session.flush()

    audit.record(
        session,
        action=f"project.{project.approval_status}",
        user_id=user_id,
        channel_id=project.channel_id,
        entity_type="content_project",
        entity_id=project.id,
        summary=f"{project.approval_status}: {project.title[:100]}"
        + (f" — {reason}" if reason else ""),
    )
    return project


def get_publish_job(session: Session, channel_id: uuid.UUID, job_id: uuid.UUID) -> PublishJob:
    job = session.get(PublishJob, job_id)
    if job is None or job.channel_id != channel_id:
        raise NotFound("Publish job not found.")
    return job


def job_to_dict(job: PublishJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "content_project_id": str(job.content_project_id),
        "status": job.status,
        "privacy_status": job.privacy_status,
        "scheduled_for": job.scheduled_for.isoformat() if job.scheduled_for else None,
        "authorized_by": job.authorized_by,
        "approved_at": job.approved_at.isoformat() if job.approved_at else None,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        "permanent_failure": job.permanent_failure,
        "last_error": job.last_error,
        "youtube_video_id": job.youtube_video_id,
        "youtube_url": (
            f"https://www.youtube.com/watch?v={job.youtube_video_id}"
            if job.youtube_video_id
            else None
        ),
        "upload_bytes": job.upload_bytes,
        "verified_at": job.verified_at.isoformat() if job.verified_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "preflight": job.preflight or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def video_to_dict(video: YouTubeVideo) -> dict[str, Any]:
    return {
        "id": str(video.id),
        "youtube_video_id": video.youtube_video_id,
        "url": f"https://www.youtube.com/watch?v={video.youtube_video_id}",
        "title": video.title,
        "privacy_status": video.privacy_status,
        "upload_status": video.upload_status,
        "published_at": video.published_at.isoformat() if video.published_at else None,
        "duration_seconds": video.duration_seconds,
        "thumbnail_url": video.thumbnail_url,
        "last_synced_at": video.last_synced_at.isoformat() if video.last_synced_at else None,
        "content_project_id": (
            str(video.content_project_id) if video.content_project_id else None
        ),
    }


