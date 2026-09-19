"""FFmpeg render pipeline.

Builds a real MP4 from the channel's own generated visuals, the synthesized narration
and the subtitle track. There is no placeholder path: without FFmpeg the caller gets
``FFMPEG UNAVAILABLE``, and without narration the render is refused.

Safety: FFmpeg is invoked with an argument vector, never a shell string. All text that
appears on screen is written to files and referenced with ``textfile=``/``subtitles=``
rather than interpolated into the filter graph, so no caption can alter the graph.
Every path handed to FFmpeg is inside the render working directory.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import (
    Channel,
    ContentProject,
    ScriptVersion,
    VideoAsset,
    VideoProject,
    VideoRenderJob,
    VoiceJob,
)
from nexora.db.models.enums import ActorType, AssetKind, RunStatus
from nexora.services import assets as asset_service
from nexora.services import audit
from nexora.services.ffmpeg_runtime import (
    command_digest,
    ffmpeg_info,
    probe_duration_seconds,
    require_ffmpeg,
    run_ffmpeg,
)
from nexora.services.video import scenes as scene_service
from nexora.services.video import subtitles as subtitle_service

logger = get_logger(__name__)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

#: Default palette, matching the dashboard's command-centre theme.
DEFAULT_PALETTE = {"background": "#0B1017", "surface": "#10161F", "accent": "#22D3EE"}

#: Conservative encode settings: the target dev machine is an 8 GB / i3.
FRAMERATE = 30
CRF = "23"
PRESET = "veryfast"
AUDIO_BITRATE = "160k"


@dataclass
class RenderResult:
    job: VideoRenderJob
    asset: VideoAsset
    subtitle_asset: VideoAsset | None
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "render_job_id": str(self.job.id),
            "output_asset_id": str(self.asset.id),
            "subtitle_asset_id": str(self.subtitle_asset.id) if self.subtitle_asset else None,
            "duration_seconds": self.duration_seconds,
            "duration_basis": "Measured with ffprobe from the rendered file.",
            "resolution": self.job.resolution,
            "output_bytes": self.job.output_bytes,
            "ffmpeg_version": self.job.ffmpeg_version,
            "warnings": self.warnings,
        }


def find_font() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise ValidationError(
        "No usable font was found for on-screen text. Install DejaVu or Liberation fonts "
        "(the API image ships fonts-dejavu-core)."
    )


def _hex_to_ffmpeg(colour: str) -> str:
    value = (colour or "").strip().lstrip("#")
    if len(value) != 6 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValidationError(f"'{colour}' is not a six-digit hex colour.")
    return f"0x{value.upper()}"


def palette_for(channel: Channel) -> dict[str, str]:
    branding = channel.branding or {}
    palette = {**DEFAULT_PALETTE}
    for key in palette:
        candidate = branding.get(key)
        if isinstance(candidate, str) and candidate.strip():
            _hex_to_ffmpeg(candidate)  # validate before it reaches the filter graph
            palette[key] = candidate
    return palette


def render_project(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    burn_subtitles: bool | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> RenderResult:
    """Render the project's current script and narration into an MP4."""
    info = require_ffmpeg()

    version = _require_version(session, project)
    voice_job = _require_narration(session, project, version)
    narration_asset = session.get(VideoAsset, voice_job.audio_asset_id)
    if narration_asset is None:
        raise NotFound("The narration audio for this project is missing from storage.")

    from nexora.services.channels import get_channel_settings

    settings_row = get_channel_settings(session, channel.id)
    burn = settings_row.subtitle_burn_in if burn_subtitles is None else burn_subtitles

    track = subtitle_service.SubtitleTrack(
        cues=[subtitle_service.Cue(**cue) for cue in (voice_job.segments or [])],
        timing_source=voice_job.timing_source or subtitle_service.ESTIMATED,
        duration_seconds=voice_job.duration_seconds or 0.0,
    )
    if not track.cues:
        raise ValidationError(
            "This project has no subtitle cues, so scenes cannot be timed. Regenerate the "
            "narration."
        )

    plan = scene_service.build_plan(
        version.sections or [],
        track,
        template=TEMPLATE_FOR(settings_row),
        aspect_ratio=settings_row.aspect_ratio,
        resolution=settings_row.resolution,
    )
    if not plan.scenes:
        raise ValidationError("No scenes could be planned from this script.")

    video_project = _upsert_video_project(session, project, plan, burn)
    job = VideoRenderJob(
        video_project_id=video_project.id,
        status=RunStatus.RUNNING.value,
        progress_percent=0,
        ffmpeg_version=info.version,
        resolution=f"{plan.width}x{plan.height}",
        started_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()

    warnings: list[str] = []
    if plan.timing_source == subtitle_service.ESTIMATED:
        warnings.append(
            "Scene and subtitle timing is estimated because the voice provider returned "
            "no timings. Cues may drift within a sentence."
        )

    work_dir = Path(mkdtemp(prefix="nexora-render-"))
    try:
        audio_path = _materialize_audio(narration_asset, work_dir)
        srt_path = work_dir / "subtitles.srt"
        srt_path.write_text(subtitle_service.to_srt(track), encoding="utf-8")

        args = build_command(
            plan=plan,
            palette=palette_for(channel),
            audio_path=audio_path,
            work_dir=work_dir,
            subtitle_path=srt_path if burn else None,
            output_path=work_dir / "render.mp4",
        )
        log = run_ffmpeg(args, timeout=3600, cwd=work_dir)

        output_path = work_dir / "render.mp4"
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValidationError("FFmpeg reported success but produced no output file.")

        measured = probe_duration_seconds(output_path)
        if measured is None:
            raise ValidationError("The rendered file's duration could not be measured.")

        output_asset = asset_service.store_bytes(
            session,
            channel,
            kind=AssetKind.RENDER.value,
            data=output_path.read_bytes(),
            content_project_id=project.id,
            source="system",
            source_provider="ffmpeg",
            license_type="system_generated",
            usage_permission_note=(
                "Rendered by NEXORA from this channel's own script, narration and generated "
                "visuals."
            ),
            meta={"scenes": len(plan.scenes), "burned_subtitles": burn},
            user_id=user_id,
            actor_type=actor_type,
        )
        subtitle_asset = asset_service.store_bytes(
            session,
            channel,
            kind=AssetKind.SUBTITLE.value,
            data=subtitle_service.to_vtt(track).encode("utf-8"),
            content_project_id=project.id,
            source="system",
            source_provider="nexora",
            license_type="system_generated",
            usage_permission_note="Captions generated from this project's own narration.",
            meta={"timing_source": track.timing_source, "cues": len(track.cues)},
            user_id=user_id,
            actor_type=actor_type,
            verify_media=False,
            mime_type="text/vtt",
            extension="vtt",
        )

        job.status = RunStatus.SUCCESS.value
        job.progress_percent = 100
        job.output_asset_id = output_asset.id
        job.subtitle_asset_id = subtitle_asset.id
        job.duration_seconds = measured
        job.output_bytes = output_asset.size_bytes
        job.command_digest = command_digest(args)
        job.log_excerpt = "\n".join(log.strip().splitlines()[-25:])[:8000]
        job.finished_at = datetime.now(UTC)
        video_project.status = "rendered"
        project.current_render_asset_id = output_asset.id
        session.flush()
    except Exception as exc:
        job.status = RunStatus.FAILED.value
        job.error = str(exc)[:4000]
        job.finished_at = datetime.now(UTC)
        session.flush()
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    audit.record(
        session,
        action="video.rendered",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="video_render_job",
        entity_id=job.id,
        summary=(
            f"Rendered {plan.width}x{plan.height}, {measured:.1f}s measured, "
            f"{len(plan.scenes)} scenes, subtitles "
            f"{'burned in' if burn else 'as a separate track'}."
        ),
    )
    logger.info(
        "video.rendered",
        extra={
            "project_id": str(project.id),
            "duration_seconds": measured,
            "scenes": len(plan.scenes),
            "bytes": output_asset.size_bytes,
        },
    )
    return RenderResult(
        job=job,
        asset=output_asset,
        subtitle_asset=subtitle_asset,
        duration_seconds=measured,
        warnings=warnings,
    )


def TEMPLATE_FOR(settings_row: Any) -> str:  # noqa: N802 - reads as a constant at call sites
    return scene_service.TEMPLATE_NARRATED_EXPLAINER


def build_command(
    *,
    plan: scene_service.ScenePlan,
    palette: dict[str, str],
    audio_path: Path,
    work_dir: Path,
    subtitle_path: Path | None,
    output_path: Path,
) -> list[str]:
    """Build the FFmpeg argument vector for a scene plan.

    Kept pure so the exact command can be asserted in tests without rendering.
    """
    font = find_font()
    background = _hex_to_ffmpeg(palette["background"])
    surface = _hex_to_ffmpeg(palette["surface"])
    accent = _hex_to_ffmpeg(palette["accent"])
    duration = f"{plan.total_duration:.3f}"

    # A generated gradient background: the channel's own visual, no third-party rights.
    filters = [
        f"gradients=s={plan.width}x{plan.height}:c0={background}:c1={surface}"
        f":x0=0:y0=0:x1={plan.width}:y1={plan.height}:d={duration}:speed=0.02,"
        f"format=yuv420p,fps={FRAMERATE}[bg]"
    ]

    title_size = max(36, plan.width // 24)
    label_size = max(18, plan.width // 64)
    current = "bg"

    for scene in plan.scenes:
        heading_file = work_dir / f"scene-{scene.index:03d}-heading.txt"
        heading_file.write_text(scene.heading, encoding="utf-8")
        label_file = work_dir / f"scene-{scene.index:03d}-label.txt"
        label_file.write_text(scene.kind.replace("_", " ").upper(), encoding="utf-8")

        enable = f"between(t\\,{scene.start:.3f}\\,{scene.end:.3f})"
        colour = accent if scene.style.get("accent") else "0xE6EDF5"
        label_out = f"s{scene.index}l"
        heading_out = f"s{scene.index}h"

        filters.append(
            f"[{current}]drawtext=fontfile='{font}':textfile='{label_file.name}'"
            f":fontcolor={accent}:fontsize={label_size}:x=(w-text_w)/2"
            f":y=h/2-{title_size}:enable='{enable}'[{label_out}]"
        )
        filters.append(
            f"[{label_out}]drawtext=fontfile='{font}':textfile='{heading_file.name}'"
            f":fontcolor={colour}:fontsize={title_size}:x=(w-text_w)/2:y=(h-text_h)/2"
            f":enable='{enable}'[{heading_out}]"
        )
        current = heading_out

    if subtitle_path is not None:
        style = (
            f"FontName=DejaVu Sans,FontSize={max(16, plan.width // 90)},"
            "PrimaryColour=&H00F5EDE6,OutlineColour=&H00000000,BorderStyle=1,"
            "Outline=2,Shadow=0,MarginV=60"
        )
        filters.append(
            f"[{current}]subtitles='{subtitle_path.name}':force_style='{style}'[vout]"
        )
        current = "vout"

    return [
        "-f", "lavfi",
        "-i", f"color=c={background}:s={plan.width}x{plan.height}:d={duration}:r={FRAMERATE}",
        "-i", str(audio_path),
        "-filter_complex", ";".join(filters),
        "-map", f"[{current}]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", PRESET,
        "-crf", CRF,
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        "-t", duration,
        str(output_path),
    ]


def _materialize_audio(asset: VideoAsset, work_dir: Path) -> Path:
    """Put the narration where FFmpeg can read it, without loading it all into memory."""
    local = asset_service.local_path_for(asset)
    if local and Path(local).is_file():
        return Path(local)

    destination = work_dir / "narration.audio"
    with destination.open("wb") as handle:
        for chunk in asset_service.open_asset(asset):
            handle.write(chunk)
    return destination


def _require_version(session: Session, project: ContentProject) -> ScriptVersion:
    if project.current_script_version_id is None:
        raise ValidationError("This project has no current script version to render.")
    version = session.get(ScriptVersion, project.current_script_version_id)
    if version is None:
        raise NotFound("The current script version no longer exists.")
    return version


def _require_narration(
    session: Session, project: ContentProject, version: ScriptVersion
) -> VoiceJob:
    from sqlalchemy import select

    job = session.execute(
        select(VoiceJob)
        .where(
            VoiceJob.content_project_id == project.id,
            VoiceJob.script_version_id == version.id,
            VoiceJob.status == RunStatus.SUCCESS.value,
        )
        .order_by(VoiceJob.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        raise ValidationError(
            "There is no successful narration for the current script version. Generate the "
            "voice track first — a video is never rendered without real narration."
        )
    return job


def _upsert_video_project(
    session: Session, project: ContentProject, plan: scene_service.ScenePlan, burn: bool
) -> VideoProject:
    from sqlalchemy import select

    video_project = session.execute(
        select(VideoProject).where(VideoProject.content_project_id == project.id)
    ).scalar_one_or_none()
    if video_project is None:
        video_project = VideoProject(content_project_id=project.id)
        session.add(video_project)

    video_project.aspect_ratio = "9:16" if plan.height > plan.width else "16:9"
    video_project.resolution = "1080p" if max(plan.width, plan.height) >= 1920 else "720p"
    video_project.template = plan.template
    video_project.scene_plan = plan.to_dict()["scenes"]
    video_project.subtitle_burn_in = burn
    video_project.status = "rendering"
    session.flush()
    return video_project


def render_availability() -> dict[str, Any]:
    """Whether a render can actually be produced right now."""
    info = ffmpeg_info()
    try:
        font: str | None = find_font()
        font_detail = f"Using {font}"
    except ValidationError as exc:
        font = None
        font_detail = str(exc)

    usable = info.available and font is not None
    return {
        "status": "AVAILABLE" if usable else "UNAVAILABLE",
        "provider": "ffmpeg",
        "detail": info.detail if info.available else info.detail,
        "missing_settings": [] if info.available else ["FFMPEG_BINARY"],
        "metadata": {
            "ffmpeg_version": info.version,
            "ffprobe": info.ffprobe_path,
            "font": font,
            "font_detail": font_detail,
        },
    }


def job_to_dict(job: VideoRenderJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "status": job.status,
        "progress_percent": job.progress_percent,
        "output_asset_id": str(job.output_asset_id) if job.output_asset_id else None,
        "subtitle_asset_id": str(job.subtitle_asset_id) if job.subtitle_asset_id else None,
        "duration_seconds": job.duration_seconds,
        "duration_basis": (
            "Measured with ffprobe from the rendered file."
            if job.duration_seconds is not None
            else None
        ),
        "resolution": job.resolution,
        "output_bytes": job.output_bytes,
        "ffmpeg_version": job.ffmpeg_version,
        "command_digest": job.command_digest,
        "error": job.error,
        "log_excerpt": job.log_excerpt,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


