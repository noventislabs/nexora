"""Helpers that build real media with FFmpeg for tests.

The audio produced here is a synthesized tone, used only to exercise the render and
measurement paths. It is never narration: production audio always comes from a
configured voice provider, and no code path substitutes this for it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import mkdtemp

from nexora.services.ffmpeg_runtime import ffmpeg_info


def ffmpeg_available() -> bool:
    return ffmpeg_info().available


def make_tone_mp3(seconds: float = 6.0, *, frequency: int = 220) -> bytes:
    """Produce a real MP3 of a known duration using FFmpeg."""
    work = Path(mkdtemp(prefix="nexora-test-audio-"))
    output = work / "tone.mp3"
    subprocess.run(  # noqa: S603 - fixed argv, test-only
        [
            ffmpeg_info().ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={seconds}",
            "-c:a", "libmp3lame",
            str(output),
        ],
        check=True,
        timeout=120,
    )
    return output.read_bytes()
