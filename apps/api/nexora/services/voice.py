"""Narration synthesis.

Providers cap how much text they accept per request, so narration is split on sentence
boundaries, synthesized chunk by chunk, and concatenated with FFmpeg. Duration is then
**measured** from the finished file with ffprobe — never estimated from the text.

When a provider returns character-level timings, each chunk's alignment is offset by
the measured duration of the chunks before it, so multi-chunk narration keeps real
timings end to end.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import Channel, ContentProject, ScriptVersion, VideoAsset, VoiceJob
from nexora.db.models.enums import ActorType, AssetKind, RunStatus
from nexora.services import assets as asset_service
from nexora.services import audit
from nexora.services.ffmpeg_runtime import probe_duration_seconds, require_ffmpeg, run_ffmpeg
from nexora.services.providers.voice import get_voice
from nexora.services.video import subtitles as subtitle_service

logger = get_logger(__name__)

#: Leave headroom under each provider's hard cap so a sentence is never split mid-word.
CHUNK_SAFETY_MARGIN = 200

_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")


@dataclass
class VoiceResult:
    job: VoiceJob
    asset: VideoAsset
    track: subtitle_service.SubtitleTrack
    chunks: int = 1
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_job_id": str(self.job.id),
            "audio_asset_id": str(self.asset.id),
            "provider": self.job.provider,
            "voice_id": self.job.voice_id,
            "duration_seconds": self.job.duration_seconds,
            "duration_basis": "Measured with ffprobe from the produced audio file.",
            "character_count": self.job.character_count,
            "timing_source": self.job.timing_source,
            "chunks": self.chunks,
            "cue_count": len(self.track.cues),
            "warnings": self.warnings,
        }


def split_for_provider(text: str, limit: int) -> list[str]:
    """Split narration into provider-sized chunks on sentence boundaries."""
    budget = max(200, limit - CHUNK_SAFETY_MARGIN)
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text or "") if s.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > budget:
            # A single sentence longer than the budget: break it on words.
            if current:
                chunks.append(current)
                current = ""
            words = sentence.split()
            piece = ""
            for word in words:
                candidate = f"{piece} {word}".strip()
                if len(candidate) > budget and piece:
                    chunks.append(piece)
                    piece = word
                else:
                    piece = candidate
            if piece:
                current = piece
            continue

        candidate = f"{current} {sentence}".strip()
        if len(candidate) > budget and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def generate_narration(
    session: Session,
    channel: Channel,
    project: ContentProject,
    version: ScriptVersion,
    *,
    voice_id: str | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> VoiceResult:
    """Synthesize narration for a script version and store it as a channel asset."""
    # Provider availability is checked first: "no voice provider is configured" is a
    # deployment-level fact the operator needs before any workflow complaint.
    provider = get_voice()  # raises ProviderNotConfigured when unset
    require_ffmpeg()  # needed to concatenate and to measure

    narration = (version.narration_text or "").strip()
    if not narration:
        raise ValidationError("This script version has no narration text to synthesize.")

    from nexora.services.channels import get_channel_settings

    settings_row = get_channel_settings(session, channel.id)
    resolved_voice = voice_id or settings_row.preferred_voice_id

    limit = getattr(
        __import__(
            f"nexora.services.providers.voice.{provider.name}", fromlist=["MAX_INPUT_CHARS"]
        ),
        "MAX_INPUT_CHARS",
        4000,
    )
    chunks = split_for_provider(narration, limit)
    if not chunks:
        raise ValidationError("The narration could not be split into synthesizable text.")

    job = VoiceJob(
        content_project_id=project.id,
        script_version_id=version.id,
        provider=provider.name,
        voice_id=resolved_voice,
        language=project.language,
        status=RunStatus.RUNNING.value,
        character_count=len(narration),
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()

    warnings: list[str] = []
    try:
        audio, alignment, chunk_count = _synthesize(provider, chunks, resolved_voice, project.language)
    except Exception as exc:
        job.status = RunStatus.FAILED.value
        job.error = str(exc)[:4000]
        job.finished_at = datetime.now(UTC)
        session.flush()
        raise

    asset = asset_service.store_bytes(
        session,
        channel,
        kind=AssetKind.NARRATION.value,
        data=audio,
        content_project_id=project.id,
        source="ai",
        source_provider=provider.name,
        # Synthesized for this channel from its own script: no third-party rights attach.
        license_type="ai_generated",
        usage_permission_note=(
            f"Narration synthesized by {provider.name} from this project's own script."
        ),
        meta={"voice_id": resolved_voice, "chunks": chunk_count},
        user_id=user_id,
        actor_type=actor_type,
        verify_media=True,
    )

    duration = asset.duration_seconds
    if duration is None or duration <= 0:
        job.status = RunStatus.FAILED.value
        job.error = "The narration duration could not be measured from the produced file."
        job.finished_at = datetime.now(UTC)
        session.flush()
        raise ValidationError(job.error)

    if alignment is None and provider.provides_timings:
        warnings.append(
            f"{provider.name} normally returns timings but did not this time; subtitle "
            "timing was estimated instead."
        )

    track = subtitle_service.build_track(
        narration, duration_seconds=duration, alignment=alignment
    )
    track.warnings.extend(warnings)

    job.status = RunStatus.SUCCESS.value
    job.audio_asset_id = asset.id
    job.duration_seconds = duration
    job.mime_type = asset.mime_type
    job.timing_source = track.timing_source
    job.segments = [cue.to_dict() for cue in track.cues]
    job.finished_at = datetime.now(UTC)
    project.narration_asset_id = asset.id
    session.flush()

    audit.record(
        session,
        action="voice.generated",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="voice_job",
        entity_id=job.id,
        summary=(
            f"Narration for script v{version.version}: {duration:.1f}s measured, "
            f"{len(narration)} characters over {chunk_count} request(s) via "
            f"{provider.name}; subtitle timing {track.timing_source}."
        ),
    )
    logger.info(
        "voice.generated",
        extra={
            "project_id": str(project.id),
            "provider": provider.name,
            "duration_seconds": duration,
            "timing_source": track.timing_source,
            "chunks": chunk_count,
        },
    )
    return VoiceResult(
        job=job, asset=asset, track=track, chunks=chunk_count, warnings=warnings
    )


def _synthesize(
    provider: Any, chunks: list[str], voice_id: str | None, language: str
) -> tuple[bytes, list[dict[str, Any]] | None, int]:
    """Synthesize each chunk and join them, carrying timings across the join."""
    pieces: list[bytes] = []
    alignments: list[list[dict[str, Any]] | None] = []

    for chunk in chunks:
        generated = provider.generate_audio(chunk, voice_id=voice_id, language=language)
        pieces.append(generated.audio)
        alignments.append(generated.alignment)

    if len(pieces) == 1:
        return pieces[0], alignments[0], 1

    with TemporaryDirectory(prefix="nexora-voice-") as work:
        work_dir = Path(work)
        paths = []
        durations = []
        for index, piece in enumerate(pieces):
            path = work_dir / f"part-{index:03d}.mp3"
            path.write_bytes(piece)
            paths.append(path)
            durations.append(probe_duration_seconds(path) or 0.0)

        # The concat demuxer needs a manifest; paths are ours, inside a temp directory.
        manifest = work_dir / "parts.txt"
        manifest.write_text("\n".join(f"file '{path.name}'" for path in paths) + "\n")

        output = work_dir / "narration.mp3"
        run_ffmpeg(
            [
                "-f", "concat",
                "-safe", "0",
                "-i", str(manifest),
                "-c", "copy",
                str(output),
            ],
            timeout=600,
            cwd=work_dir,
        )
        joined = output.read_bytes()

    if any(alignment is None for alignment in alignments):
        return joined, None, len(pieces)

    # Offset each chunk's timings by the measured duration of everything before it.
    merged: list[dict[str, Any]] = []
    offset = 0.0
    for alignment, duration in zip(alignments, durations, strict=True):
        for record in alignment or []:
            merged.append(
                {
                    "character": record["character"],
                    "start": record["start"] + offset,
                    "end": record["end"] + offset,
                }
            )
        offset += duration
    return joined, merged, len(pieces)


def require_provider() -> None:
    """Raise ProviderNotConfigured if narration cannot be produced at all."""
    get_voice()


def get_job(session: Session, project_id: uuid.UUID, job_id: uuid.UUID) -> VoiceJob:
    job = session.get(VoiceJob, job_id)
    if job is None or job.content_project_id != project_id:
        raise NotFound("Voice job not found.")
    return job


def job_to_dict(job: VoiceJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "status": job.status,
        "provider": job.provider,
        "voice_id": job.voice_id,
        "language": job.language,
        "audio_asset_id": str(job.audio_asset_id) if job.audio_asset_id else None,
        "character_count": job.character_count,
        "duration_seconds": job.duration_seconds,
        "duration_basis": (
            "Measured with ffprobe from the produced audio file."
            if job.duration_seconds is not None
            else None
        ),
        "timing_source": job.timing_source,
        "timing_is_estimated": job.timing_source == subtitle_service.ESTIMATED,
        "cue_count": len(job.segments or []),
        "mime_type": job.mime_type,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def voice_status() -> dict[str, Any]:
    """Voice availability for the UI, including quota where the provider exposes it."""
    from nexora.services.availability import voice_availability

    availability = voice_availability()
    payload: dict[str, Any] = {**availability.to_dict(), "voices": [], "status_detail": None}
    if not availability.usable:
        return payload

    try:
        provider = get_voice()
        payload["voices"] = [
            {"id": voice.id, "name": voice.name, "languages": list(voice.languages)}
            for voice in provider.list_voices()
        ]
        payload["status_detail"] = provider.get_status()
        payload["provides_timings"] = provider.provides_timings
    except Exception as exc:
        # Configured but unreachable is a different state from not configured.
        payload["status"] = "UNAVAILABLE"
        payload["detail"] = f"{availability.detail} — provider call failed: {exc}"
    return payload


