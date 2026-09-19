"""Rendering: scene planning, and a real FFmpeg render producing a real MP4.

Nothing here is simulated. FFmpeg runs, and the assertions are made against the file
it actually produced.
"""

from __future__ import annotations

import base64
import subprocess
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import ValidationError
from nexora.db.models import (
    AuditLog,
    Channel,
    ContentProject,
    ScriptVersion,
    VideoAsset,
    VideoRenderJob,
)
from nexora.db.models.enums import RunStatus
from nexora.services import voice as voice_service
from nexora.services.ffmpeg_runtime import ffmpeg_info, probe_duration_seconds
from nexora.services.video import render as render_service
from nexora.services.video import scenes as scene_service
from nexora.services.video import subtitles as subtitle_service
from tests.media_helpers import ffmpeg_available, make_tone_mp3

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg is not installed")

SECTIONS = [
    {
        "kind": "hook",
        "heading": "Opening",
        "narration": "Two industry bodies looked at the same quarter.",
        "document_indices": [0],
    },
    {
        "kind": "evidence",
        "heading": "What they say",
        "narration": "One puts the figure near forty thousand. Another places it closer to "
        "twenty-five thousand. Both agree packaging is the limit.",
        "document_indices": [0, 1],
    },
    {
        "kind": "conclusion",
        "heading": "Where that leaves us",
        "narration": "Until reconciled, the scale is disputed.",
        "document_indices": [0],
    },
]
NARRATION = "\n\n".join(section["narration"] for section in SECTIONS)


# -------------------------------------------------------------------- scene plans
def test_every_section_gets_a_scene_and_the_plan_covers_the_audio() -> None:
    track = subtitle_service.build_track(NARRATION, duration_seconds=30.0)
    plan = scene_service.build_plan(SECTIONS, track, aspect_ratio="16:9", resolution="1080p")

    assert len(plan.scenes) == 3
    assert [scene.kind for scene in plan.scenes] == ["hook", "evidence", "conclusion"]
    assert plan.scenes[0].start == 0.0
    assert plan.scenes[-1].end == 30.0
    # No gaps between scenes.
    for previous, following in zip(plan.scenes, plan.scenes[1:], strict=False):
        assert previous.end == following.start


def test_plan_carries_the_timing_provenance() -> None:
    estimated = scene_service.build_plan(
        SECTIONS, subtitle_service.build_track(NARRATION, duration_seconds=30.0)
    )
    assert estimated.timing_source == "estimated"
    assert estimated.to_dict()["timing_is_estimated"] is True

    alignment = [
        {"character": character, "start": index * 0.05, "end": (index + 1) * 0.05}
        for index, character in enumerate(NARRATION)
    ]
    timed = scene_service.build_plan(
        SECTIONS,
        subtitle_service.build_track(
            NARRATION, duration_seconds=len(NARRATION) * 0.05, alignment=alignment
        ),
    )
    assert timed.timing_source == "provider"
    assert timed.to_dict()["timing_is_estimated"] is False


def test_resolutions_and_aspect_ratios() -> None:
    assert scene_service.resolve_dimensions("16:9", "1080p") == (1920, 1080)
    assert scene_service.resolve_dimensions("9:16", "1080p") == (1080, 1920)
    assert scene_service.resolve_dimensions("16:9", "720p") == (1280, 720)
    with pytest.raises(ValueError):
        scene_service.resolve_dimensions("4:3", "1080p")


def test_an_unknown_template_is_rejected() -> None:
    track = subtitle_service.build_track(NARRATION, duration_seconds=10.0)
    with pytest.raises(ValueError):
        scene_service.build_plan(SECTIONS, track, template="cinematic-masterpiece")


# --------------------------------------------------------------- command building
def test_command_never_interpolates_caption_text_into_the_filter_graph(tmp_path) -> None:
    """On-screen text reaches FFmpeg as a file reference, never inside the graph."""
    hostile = [
        {
            "kind": "hook",
            "heading": "Nasty: 'quote' \\ colon: and [brackets]",
            "narration": "Some narration text for the hostile heading case here.",
            "document_indices": [],
        }
    ]
    track = subtitle_service.build_track(hostile[0]["narration"], duration_seconds=5.0)
    plan = scene_service.build_plan(hostile, track, resolution="720p")

    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"ID3\x04\x00")
    args = render_service.build_command(
        plan=plan,
        palette=render_service.DEFAULT_PALETTE,
        audio_path=audio,
        work_dir=tmp_path,
        subtitle_path=None,
        output_path=tmp_path / "out.mp4",
    )
    graph = args[args.index("-filter_complex") + 1]
    assert "Nasty" not in graph, "heading text must not appear in the filter graph"
    assert "textfile=" in graph
    # The text went to a file instead.
    assert (tmp_path / "scene-000-heading.txt").read_text() == hostile[0]["heading"]


def test_command_passes_the_argument_guard(tmp_path) -> None:
    from nexora.services.ffmpeg_runtime import validate_args

    track = subtitle_service.build_track(NARRATION, duration_seconds=10.0)
    plan = scene_service.build_plan(SECTIONS, track, resolution="720p")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"ID3\x04\x00")
    subtitle = tmp_path / "subs.srt"
    subtitle.write_text(subtitle_service.to_srt(track))

    args = render_service.build_command(
        plan=plan,
        palette=render_service.DEFAULT_PALETTE,
        audio_path=audio,
        work_dir=tmp_path,
        subtitle_path=subtitle,
        output_path=tmp_path / "out.mp4",
    )
    validate_args(args)  # must not raise


def test_an_invalid_brand_colour_is_rejected(tmp_path) -> None:
    track = subtitle_service.build_track(NARRATION, duration_seconds=10.0)
    plan = scene_service.build_plan(SECTIONS, track, resolution="720p")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"ID3\x04\x00")
    with pytest.raises(ValidationError):
        render_service.build_command(
            plan=plan,
            palette={**render_service.DEFAULT_PALETTE, "accent": "red; rm -rf /"},
            audio_path=audio,
            work_dir=tmp_path,
            subtitle_path=None,
            output_path=tmp_path / "out.mp4",
        )


# ------------------------------------------------------------------- real renders
@requires_ffmpeg
def test_ffmpeg_produces_a_real_playable_mp4(tmp_path) -> None:
    from nexora.services.ffmpeg_runtime import run_ffmpeg

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(make_tone_mp3(seconds=8.0))
    measured_audio = probe_duration_seconds(audio)
    assert measured_audio is not None and 7.5 < measured_audio < 8.5

    track = subtitle_service.build_track(NARRATION, duration_seconds=measured_audio)
    plan = scene_service.build_plan(SECTIONS, track, resolution="720p")
    subtitle = tmp_path / "subs.srt"
    subtitle.write_text(subtitle_service.to_srt(track), encoding="utf-8")

    output = tmp_path / "render.mp4"
    args = render_service.build_command(
        plan=plan,
        palette=render_service.DEFAULT_PALETTE,
        audio_path=audio,
        work_dir=tmp_path,
        subtitle_path=subtitle,
        output_path=output,
    )
    run_ffmpeg(args, timeout=600, cwd=tmp_path)

    assert output.is_file() and output.stat().st_size > 10_000
    rendered = probe_duration_seconds(output)
    assert rendered is not None
    assert abs(rendered - measured_audio) < 0.5, "render length must match the narration"

    streams = _probe_streams(output)
    kinds = {stream["codec_type"] for stream in streams}
    assert kinds == {"video", "audio"}, "a render must carry both picture and sound"
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["codec_name"] == "h264"


@requires_ffmpeg
def test_the_rendered_frames_actually_contain_the_drawn_text(tmp_path) -> None:
    """A black video would satisfy a duration check; this proves pixels were drawn."""
    from PIL import Image

    from nexora.services.ffmpeg_runtime import run_ffmpeg

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(make_tone_mp3(seconds=8.0))
    duration = probe_duration_seconds(audio) or 8.0

    track = subtitle_service.build_track(NARRATION, duration_seconds=duration)
    plan = scene_service.build_plan(SECTIONS, track, resolution="720p")
    subtitle = tmp_path / "subs.srt"
    subtitle.write_text(subtitle_service.to_srt(track), encoding="utf-8")
    output = tmp_path / "render.mp4"
    run_ffmpeg(
        render_service.build_command(
            plan=plan,
            palette=render_service.DEFAULT_PALETTE,
            audio_path=audio,
            work_dir=tmp_path,
            subtitle_path=subtitle,
            output_path=output,
        ),
        timeout=600,
        cwd=tmp_path,
    )

    frame = tmp_path / "frame.png"
    subprocess.run(  # noqa: S603 - fixed argv, test-only
        [
            ffmpeg_info().ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "4", "-i", str(output), "-frames:v", "1", str(frame),
        ],
        check=True,
        timeout=120,
    )
    with Image.open(frame) as image:
        greyscale = image.convert("L")
        pixels = list(greyscale.getdata())

    bright = sum(1 for value in pixels if value > 120)
    assert bright > 1000, "the frame is blank — no text or subtitles were drawn"
    assert bright < len(pixels) * 0.5, "the frame is washed out, not a dark composition"


def _probe_streams(path) -> list[dict]:
    import json

    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [
            ffmpeg_info().ffprobe_path or "ffprobe",
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)["streams"]


# ------------------------------------------------------- render through the service
@pytest.fixture
def project_with_narration(db: Session, channel: Channel, user):
    """A project with a script and a real measured narration asset."""
    from nexora.db.models import ContentScript

    project = ContentProject(
        channel_id=channel.id,
        title="Why the numbers disagree",
        status="assets",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(project)
    db.flush()
    script = ContentScript(content_project_id=project.id, current_version=1, status="generated")
    db.add(script)
    db.flush()

    version = ScriptVersion(
        script_id=script.id,
        version=1,
        sections=SECTIONS,
        plain_text=NARRATION,
        narration_text=NARRATION,
        source_references=[],
        word_count=len(NARRATION.split()),
        estimated_duration_seconds=30,
        created_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    project.current_script_version_id = version.id
    db.commit()
    return project, version


@requires_ffmpeg
@respx.mock
def test_full_narration_then_render_through_the_services(
    db: Session, channel: Channel, project_with_narration, monkeypatch
) -> None:
    project, version = project_with_narration
    monkeypatch.setattr(settings, "voice_provider", "elevenlabs")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "fixture-voice-1")

    # The provider is intercepted; the audio it returns is real MP3 bytes, and every
    # duration below is measured from that file rather than assumed.
    tone = make_tone_mp3(seconds=9.0)
    respx.post(
        "https://api.elevenlabs.io/v1/text-to-speech/fixture-voice-1/with-timestamps"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(tone).decode(),
                "alignment": {
                    "characters": list(NARRATION),
                    "character_start_times_seconds": [
                        i * (9.0 / len(NARRATION)) for i in range(len(NARRATION))
                    ],
                    "character_end_times_seconds": [
                        (i + 1) * (9.0 / len(NARRATION)) for i in range(len(NARRATION))
                    ],
                },
            },
        )
    )

    voice_result = voice_service.generate_narration(db, channel, project, version)
    db.commit()

    assert voice_result.job.status == RunStatus.SUCCESS.value
    assert voice_result.job.timing_source == "provider"
    measured = voice_result.job.duration_seconds
    assert measured is not None and 8.5 < measured < 9.5, "duration must be measured, not assumed"
    assert voice_result.asset.mime_type == "audio/mpeg"
    assert voice_result.asset.license_status == "PERMITTED"

    render_result = render_service.render_project(db, channel, project, burn_subtitles=True)
    db.commit()

    job = render_result.job
    assert job.status == RunStatus.SUCCESS.value
    assert job.resolution == "1920x1080"
    assert job.output_bytes and job.output_bytes > 10_000
    assert job.ffmpeg_version
    assert job.command_digest
    assert abs(render_result.duration_seconds - measured) < 0.6

    output = db.get(VideoAsset, job.output_asset_id)
    assert output is not None and output.kind == "render"
    assert output.license_status == "PERMITTED"
    assert output.duration_seconds is not None

    captions = db.get(VideoAsset, job.subtitle_asset_id)
    assert captions is not None and captions.mime_type == "text/vtt"
    body = b"".join(
        __import__("nexora.services.assets", fromlist=["open_asset"]).open_asset(captions)
    ).decode()
    assert body.startswith("WEBVTT")
    assert "NOTE" not in body, "provider-timed captions carry no estimation note"

    assert project.current_render_asset_id == output.id
    assert db.query(AuditLog).filter(AuditLog.action == "video.rendered").count() == 1


def test_render_without_narration_is_refused(
    db: Session, channel: Channel, project_with_narration
) -> None:
    project, _ = project_with_narration
    with pytest.raises(ValidationError) as exc:
        render_service.render_project(db, channel, project)
    assert "never rendered without real narration" in exc.value.message
    assert db.query(VideoRenderJob).count() == 0


def test_render_capability_reflects_the_real_toolchain() -> None:
    capability = render_service.render_availability()
    info = ffmpeg_info()
    assert capability["status"] == ("AVAILABLE" if info.available else "UNAVAILABLE")
    if info.available:
        assert capability["metadata"]["ffmpeg_version"] == info.version
        assert capability["metadata"]["font"]


def test_missing_ffmpeg_reports_unavailable(monkeypatch) -> None:
    """The honest failure: no binary means no render, not a placeholder file."""
    from nexora.services.ffmpeg_runtime import FFmpegUnavailable, require_ffmpeg

    monkeypatch.setattr(settings, "ffmpeg_binary", "definitely-not-ffmpeg")
    ffmpeg_info(refresh=True)
    try:
        assert render_service.render_availability()["status"] == "UNAVAILABLE"
        with pytest.raises(FFmpegUnavailable):
            require_ffmpeg()
    finally:
        monkeypatch.undo()
        ffmpeg_info(refresh=True)
