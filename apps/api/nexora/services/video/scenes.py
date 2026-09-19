"""Scene planning.

A scene is one script section, timed against the narration that was actually produced.
Scene boundaries are taken from the subtitle cues, so when the voice provider returned
real timings the scenes line up with the audio exactly; when it did not, the scenes
inherit the same estimated timing and say so.

No scene invents a duration, and the plan always sums to the measured audio length.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexora.services.video.subtitles import Cue, SubtitleTrack

#: Built-in scene templates. Each maps a section kind to how it is presented.
TEMPLATE_NARRATED_EXPLAINER = "narrated_explainer"
TEMPLATE_TITLE_SEQUENCE = "title_sequence"
TEMPLATES = (TEMPLATE_NARRATED_EXPLAINER, TEMPLATE_TITLE_SEQUENCE)

#: Presentation per section kind. These are the channel's own generated visuals, so no
#: third-party licence attaches to them.
SECTION_STYLES: dict[str, dict[str, Any]] = {
    "hook": {"emphasis": "high", "accent": True},
    "introduction": {"emphasis": "medium", "accent": False},
    "section": {"emphasis": "medium", "accent": False},
    "evidence": {"emphasis": "medium", "accent": True},
    "example": {"emphasis": "low", "accent": False},
    "conclusion": {"emphasis": "high", "accent": True},
    "call_to_action": {"emphasis": "medium", "accent": True},
}

RESOLUTIONS: dict[tuple[str, str], tuple[int, int]] = {
    ("16:9", "1080p"): (1920, 1080),
    ("16:9", "720p"): (1280, 720),
    ("9:16", "1080p"): (1080, 1920),
    ("9:16", "720p"): (720, 1280),
}


@dataclass
class Scene:
    index: int
    kind: str
    heading: str
    start: float
    end: float
    cue_indices: list[int] = field(default_factory=list)
    document_indices: list[int] = field(default_factory=list)
    background_asset_id: str | None = None
    style: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "heading": self.heading,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "cue_indices": self.cue_indices,
            "document_indices": self.document_indices,
            "background_asset_id": self.background_asset_id,
            "style": self.style,
        }


@dataclass
class ScenePlan:
    scenes: list[Scene]
    total_duration: float
    timing_source: str
    template: str
    width: int
    height: int
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "width": self.width,
            "height": self.height,
            "total_duration": self.total_duration,
            "timing_source": self.timing_source,
            "timing_is_estimated": self.timing_source == "estimated",
            "note": self.note,
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


def resolve_dimensions(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    try:
        return RESOLUTIONS[(aspect_ratio, resolution)]
    except KeyError:
        raise ValueError(
            f"Unsupported combination {aspect_ratio} at {resolution}. "
            f"Supported: {', '.join(f'{a} {r}' for a, r in RESOLUTIONS)}."
        ) from None


def build_plan(
    sections: list[dict[str, Any]],
    track: SubtitleTrack,
    *,
    template: str = TEMPLATE_NARRATED_EXPLAINER,
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
) -> ScenePlan:
    """Time each script section against the cues produced from the real narration."""
    if template not in TEMPLATES:
        raise ValueError(f"Unknown scene template '{template}'. Known: {', '.join(TEMPLATES)}.")
    width, height = resolve_dimensions(aspect_ratio, resolution)

    usable = [
        section
        for section in sections
        if isinstance(section, dict) and str(section.get("narration") or "").strip()
    ]
    if not usable or not track.cues:
        return ScenePlan(
            scenes=[],
            total_duration=track.duration_seconds,
            timing_source=track.timing_source,
            template=template,
            width=width,
            height=height,
            note="There were no timed cues to build scenes from.",
        )

    assignments = _assign_cues(usable, track.cues)
    scenes: list[Scene] = []
    for section, cues in zip(usable, assignments, strict=True):
        if not cues:
            continue
        kind = str(section.get("kind") or "section")
        scenes.append(
            Scene(
                index=len(scenes),
                kind=kind,
                heading=str(section.get("heading") or kind.replace("_", " ").title())[:120],
                start=cues[0].start,
                end=cues[-1].end,
                cue_indices=[cue.index for cue in cues],
                document_indices=list(section.get("document_indices") or []),
                style=SECTION_STYLES.get(kind, SECTION_STYLES["section"]),
            )
        )

    if scenes:
        # The plan must cover exactly the audio that exists: no gap, no overrun.
        scenes[0].start = 0.0
        for previous, following in zip(scenes, scenes[1:], strict=False):
            previous.end = following.start
        scenes[-1].end = track.duration_seconds

    return ScenePlan(
        scenes=scenes,
        total_duration=track.duration_seconds,
        timing_source=track.timing_source,
        template=template,
        width=width,
        height=height,
        note=track.note,
    )


def _assign_cues(sections: list[dict[str, Any]], cues: list[Cue]) -> list[list[Cue]]:
    """Assign each cue to the section its text falls inside.

    Both the sections and the cues describe the same narration in the same order, so
    each is laid out on one character axis and a cue is assigned to the section
    containing its midpoint. Using the midpoint rather than a running budget keeps a
    cue that straddles a boundary with the section it mostly belongs to, and avoids the
    off-by-one that a per-section budget reset introduces when cue text drops the
    whitespace between sections.
    """
    assignments: list[list[Cue]] = [[] for _ in sections]

    # Section spans on the character axis.
    spans: list[tuple[int, int]] = []
    position = 0
    for section in sections:
        length = len(str(section.get("narration") or "").strip())
        spans.append((position, position + length))
        position += length

    cue_position = 0
    for cue in cues:
        midpoint = cue_position + len(cue.text) / 2
        cue_position += len(cue.text)

        index = len(sections) - 1
        for candidate, (start, end) in enumerate(spans):
            if start <= midpoint < end:
                index = candidate
                break
        assignments[index].append(cue)
    return assignments
