"""FFmpeg toolchain detection and safe invocation.

Two rules govern this module:

1. If the binary is absent the system reports ``UNAVAILABLE``. It never pretends to
   render, and it never writes a placeholder file in place of a real video.
2. FFmpeg is always invoked as an argument vector (never a shell string) and the
   argument list is built from validated, application-controlled values only. User
   input never becomes an FFmpeg flag.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from nexora.config import settings
from nexora.core.errors import NexoraError, ProviderNotConfigured, ValidationError
from nexora.core.logging import get_logger

logger = get_logger(__name__)

_VERSION_RE = re.compile(r"ffmpeg version (\S+)")

# FFmpeg is always executed with ``shell=False``, so shell metacharacters are inert —
# an argv entry containing ";" is passed to execve verbatim and cannot start a second
# command. Filtering them would be security theatre and would break legitimate syntax:
# ";" separates links in a -filter_complex graph, and ":" separates filter options.
#
# What *is* dangerous is FFmpeg's own protocol handling. A path-looking argument can
# reach the network, read an arbitrary local file, or chain inputs. Every file NEXORA
# hands FFmpeg is a local path inside the render working directory, so any protocol
# prefix indicates either a bug or an injection attempt.
FORBIDDEN_PROTOCOL_PREFIXES = (
    "http://", "https://", "ftp://", "ftps://", "tcp://", "udp://", "rtp://", "rtmp://",
    "rtsp://", "srt://", "sftp://", "gopher://", "data:", "file:", "pipe:", "concat:",
    "subfile:", "async:", "cache:", "crypto:", "unix://",
)

# Argument hygiene: a null byte truncates the string at the execve boundary, and a
# newline breaks any argument file or log line that later carries it.
FORBIDDEN_CONTROL_CHARACTERS = ("\x00", "\n", "\r")


class FFmpegUnavailable(ProviderNotConfigured):
    code = "ffmpeg_unavailable"


class FFmpegFailed(NexoraError):
    status_code = 500
    code = "ffmpeg_failed"


@dataclass(frozen=True)
class FFmpegInfo:
    available: bool
    ffmpeg_path: str | None
    ffprobe_path: str | None
    version: str | None
    detail: str


@lru_cache(maxsize=1)
def _probe() -> FFmpegInfo:
    ffmpeg_path = shutil.which(settings.ffmpeg_binary)
    ffprobe_path = shutil.which(settings.ffprobe_binary)
    if not ffmpeg_path:
        return FFmpegInfo(
            available=False,
            ffmpeg_path=None,
            ffprobe_path=ffprobe_path,
            version=None,
            detail=(
                f"'{settings.ffmpeg_binary}' was not found on PATH. Install FFmpeg "
                "(https://ffmpeg.org/download.html) or set FFMPEG_BINARY to its full path."
            ),
        )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return FFmpegInfo(
            available=False,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            version=None,
            detail=f"FFmpeg is present but could not be executed: {exc}",
        )
    if completed.returncode != 0:
        return FFmpegInfo(
            available=False,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            version=None,
            detail=f"'{ffmpeg_path} -version' exited with {completed.returncode}.",
        )
    match = _VERSION_RE.search(completed.stdout)
    version = match.group(1) if match else completed.stdout.splitlines()[0][:120]
    detail = f"FFmpeg {version}"
    if not ffprobe_path:
        detail += " (ffprobe missing — durations cannot be measured)"
    return FFmpegInfo(
        available=True,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        version=version,
        detail=detail,
    )


def ffmpeg_info(refresh: bool = False) -> FFmpegInfo:
    if refresh:
        _probe.cache_clear()
    return _probe()


def require_ffmpeg() -> FFmpegInfo:
    info = ffmpeg_info()
    if not info.available:
        raise FFmpegUnavailable(f"FFMPEG UNAVAILABLE. {info.detail}")
    return info


def validate_args(args: list[str]) -> None:
    """Reject arguments that could make FFmpeg read or write somewhere unintended.

    This does not filter shell metacharacters — see the note on
    :data:`FORBIDDEN_PROTOCOL_PREFIXES` for why that would be theatre here. The real
    guarantee comes from two properties held by construction:

    * every argument is built by NEXORA from validated values, and
    * all operator- or model-supplied text reaches FFmpeg through ``textfile=`` and
      ``subtitles=`` file references, never interpolated into the filter graph.
    """
    for arg in args:
        if not isinstance(arg, str):
            raise ValidationError("FFmpeg arguments must be strings.")
        for character in FORBIDDEN_CONTROL_CHARACTERS:
            if character in arg:
                raise ValidationError(
                    "FFmpeg argument contains a control character "
                    f"({character!r}) and was refused."
                )
        lowered = arg.lower()
        for prefix in FORBIDDEN_PROTOCOL_PREFIXES:
            if lowered.startswith(prefix) or f"'{prefix}" in lowered or f'"{prefix}' in lowered:
                raise ValidationError(
                    f"FFmpeg argument uses the '{prefix}' protocol. NEXORA only ever "
                    "passes local paths inside the render working directory."
                )


def run_ffmpeg(args: list[str], *, timeout: int = 3600, cwd: Path | None = None) -> str:
    """Run FFmpeg with an explicit argument vector and return its stderr log.

    ``args`` excludes the binary itself and the global flags added here.
    """
    info = require_ffmpeg()
    validate_args(args)
    command = [info.ffmpeg_path or settings.ffmpeg_binary, "-hide_banner", "-nostdin", "-y", *args]
    logger.info(
        "ffmpeg.run",
        extra={"arg_count": len(command), "digest": command_digest(command), "cwd": str(cwd or "")},
    )
    try:
        completed = subprocess.run(  # noqa: S603 - argv is validated above, shell=False
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegFailed(f"FFmpeg timed out after {timeout}s.") from exc
    if completed.returncode != 0:
        tail = "\n".join(completed.stderr.strip().splitlines()[-25:])
        raise FFmpegFailed(
            f"FFmpeg exited with status {completed.returncode}.", details={"log_excerpt": tail}
        )
    return completed.stderr


def probe_duration_seconds(path: Path) -> float | None:
    """Measure media duration with ffprobe, or return ``None`` if it cannot be measured."""
    info = ffmpeg_info()
    if not info.ffprobe_path:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                info.ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def command_digest(command: list[str]) -> str:
    return hashlib.sha256("\x00".join(command).encode()).hexdigest()
