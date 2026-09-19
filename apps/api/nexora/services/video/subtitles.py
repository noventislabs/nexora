"""Subtitle generation.

Two timing sources, and the difference is always carried forward:

``provider``
    The synthesizer returned per-character timings. Cue boundaries are read from them,
    so they line up with the audio exactly.

``estimated``
    The synthesizer returned audio only. Cue boundaries are interpolated by spreading
    the *measured* audio duration across the text in proportion to character count.
    This is an approximation, and everything built from it says so.

Nothing here invents a duration: the total always comes from ffprobe measuring the
file that was actually produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PROVIDER_TIMED = "provider"
ESTIMATED = "estimated"

#: Readability bounds for a cue, in the region most subtitle guidelines land on.
MAX_CUE_CHARS = 84
MAX_CUE_SECONDS = 6.0
MIN_CUE_SECONDS = 0.7

_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "start": self.start, "end": self.end, "text": self.text}


@dataclass
class SubtitleTrack:
    cues: list[Cue]
    timing_source: str
    duration_seconds: float
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_estimated(self) -> bool:
        return self.timing_source == ESTIMATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "timing_source": self.timing_source,
            "is_estimated": self.is_estimated,
            "duration_seconds": self.duration_seconds,
            "cue_count": len(self.cues),
            "note": self.note,
            "warnings": self.warnings,
            "cues": [cue.to_dict() for cue in self.cues],
        }


def split_into_cues(text: str) -> list[str]:
    """Split narration into readable cue-sized chunks on sentence boundaries."""
    chunks: list[str] = []
    for raw in _SENTENCE_RE.findall(text or ""):
        sentence = raw.strip()
        if not sentence:
            continue
        if len(sentence) <= MAX_CUE_CHARS:
            chunks.append(sentence)
            continue

        # Long sentence: break on word boundaries rather than mid-word.
        current = ""
        for word in sentence.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) > MAX_CUE_CHARS and current:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def build_track(
    narration: str, *, duration_seconds: float, alignment: list[dict[str, Any]] | None = None
) -> SubtitleTrack:
    """Build a subtitle track, using real timings when the provider supplied them."""
    if duration_seconds <= 0:
        raise ValueError("Subtitle timing needs a measured audio duration.")

    chunks = split_into_cues(narration)
    if not chunks:
        return SubtitleTrack(
            cues=[],
            timing_source=ESTIMATED,
            duration_seconds=duration_seconds,
            note="There was no narration text to caption.",
        )

    if alignment:
        track = _from_alignment(narration, chunks, alignment, duration_seconds)
        if track is not None:
            return track

    return _estimated(chunks, duration_seconds)


def _from_alignment(
    narration: str,
    chunks: list[str],
    alignment: list[dict[str, Any]],
    duration_seconds: float,
) -> SubtitleTrack | None:
    """Map cue text onto per-character timings.

    Returns ``None`` if the alignment cannot be walked cleanly, so callers fall back to
    estimation rather than emitting subtitles that drift.
    """
    characters = [str(item.get("character", "")) for item in alignment]
    joined = "".join(characters)
    warnings: list[str] = []

    cues: list[Cue] = []
    cursor = 0
    for index, chunk in enumerate(chunks, start=1):
        # Locate the chunk in the alignment stream, tolerating whitespace differences.
        position = _find_from(joined, chunk, cursor)
        if position is None:
            return None
        end_position = position + len(chunk)
        if end_position > len(alignment):
            return None

        try:
            start = float(alignment[position]["start"])
            end = float(alignment[min(end_position, len(alignment)) - 1]["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if end <= start:
            end = start + MIN_CUE_SECONDS

        cues.append(Cue(index=index, start=round(start, 3), end=round(end, 3), text=chunk))
        cursor = end_position

    if not cues:
        return None
    if cues[-1].end > duration_seconds + 1.0:
        warnings.append(
            "Provider timings run past the measured audio duration; they were clamped."
        )
        for cue in cues:
            cue.end = min(cue.end, duration_seconds)
            cue.start = min(cue.start, cue.end)

    return SubtitleTrack(
        cues=cues,
        timing_source=PROVIDER_TIMED,
        duration_seconds=duration_seconds,
        note="Cue times come from the synthesizer's own character-level alignment.",
        warnings=warnings,
    )


def _find_from(haystack: str, needle: str, start: int) -> int | None:
    position = haystack.find(needle, start)
    if position != -1:
        return position
    # Retry ignoring runs of whitespace, which some synthesizers normalize.
    collapsed_needle = re.sub(r"\s+", " ", needle)
    position = haystack.find(collapsed_needle, start)
    return position if position != -1 else None


def _estimated(chunks: list[str], duration_seconds: float) -> SubtitleTrack:
    """Distribute the measured duration across cues in proportion to their length."""
    weights = [max(len(chunk), 1) for chunk in chunks]
    total_weight = sum(weights)

    cues: list[Cue] = []
    elapsed = 0.0
    for index, (chunk, weight) in enumerate(zip(chunks, weights, strict=True), start=1):
        span = duration_seconds * (weight / total_weight)
        start = elapsed
        end = min(duration_seconds, start + span)
        if end - start < MIN_CUE_SECONDS:
            end = min(duration_seconds, start + MIN_CUE_SECONDS)
        cues.append(Cue(index=index, start=round(start, 3), end=round(end, 3), text=chunk))
        elapsed = end

    if cues:
        cues[-1].end = round(duration_seconds, 3)

    return SubtitleTrack(
        cues=cues,
        timing_source=ESTIMATED,
        duration_seconds=duration_seconds,
        note=(
            "This voice provider returns no timings, so cue boundaries are interpolated "
            "from the measured audio duration. They are approximate."
        ),
    )


def format_timestamp(seconds: float, *, separator: str) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    if milliseconds == 1000:  # rounding can carry
        milliseconds = 999
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def to_srt(track: SubtitleTrack) -> str:
    blocks = []
    for cue in track.cues:
        blocks.append(
            f"{cue.index}\n"
            f"{format_timestamp(cue.start, separator=',')} --> "
            f"{format_timestamp(cue.end, separator=',')}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(track: SubtitleTrack) -> str:
    header = "WEBVTT\n"
    if track.is_estimated:
        # A note in the file itself, so the provenance survives being downloaded.
        header += f"NOTE\n{track.note}\n"
    blocks = [header]
    for cue in track.cues:
        blocks.append(
            f"{cue.index}\n"
            f"{format_timestamp(cue.start, separator='.')} --> "
            f"{format_timestamp(cue.end, separator='.')}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)
