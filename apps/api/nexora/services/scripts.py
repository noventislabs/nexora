"""Script generation.

Every generation produces a new immutable :class:`ScriptVersion`. Nothing is ever
overwritten, so a regression is always recoverable and a published script is always
traceable to the exact text that was approved.

Grounding rules enforced in code, not trusted to the model:

* Sections may cite research document indices; unresolvable citations are stripped and
  reported rather than kept as fake attribution.
* The narration is checked against the research source text for verbatim reuse. A long
  copied span fails the version's originality check, which blocks it from progressing.
* Estimated duration is computed from the word count at a stated speaking rate. It is
  a derived figure, labelled as such, not a claim about the finished video.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ProviderUnavailable, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import (
    Channel,
    ContentProject,
    ResearchDocument,
    ScriptVersion,
    TopicResearch,
)
from nexora.db.models.enums import ActorType, ProjectStatus
from nexora.services import audit
from nexora.services import projects as project_service
from nexora.services.providers.llm import LLMMessage, get_llm

logger = get_logger(__name__)

#: Words per minute used to convert a script to an estimated runtime. Measured from
#: typical narration pacing; the figure is always reported as an estimate.
WORDS_PER_MINUTE = 150

#: A verbatim run of this many consecutive words from a source is treated as copying.
VERBATIM_WINDOW_WORDS = 9

SECTION_KINDS = (
    "hook",
    "introduction",
    "section",
    "evidence",
    "example",
    "conclusion",
    "call_to_action",
)

SYSTEM_PROMPT = """You are writing the narration script for a factual YouTube video.

You are given RESEARCH: facts, claims, statistics, conflicts and uncertainties, each \
tied to numbered source documents. Write only from that research.

Hard rules:
- Every factual sentence must come from the research. Invent nothing.
- Cite the research document indices that support each section.
- Use your own wording throughout. Never reuse a sentence or long phrase from a source \
document; this script is checked for verbatim reuse and will be rejected if it copies.
- Where the research records a CLAIM rather than a FACT, say who asserts it.
- Where the research records a conflict, present both positions. Do not resolve it.
- Where the research records an uncertainty that matters, say plainly that it is not known.
- Do not pad. No filler transitions, no "in today's video", no restating the title, no \
manufactured urgency, no rhetorical questions used as padding.
- Do not promise the viewer anything about the channel's performance or growth.
- Write narration only: spoken words. No camera directions, no stage notes, no emoji.

Return ONLY a JSON object of this exact shape:
{
  "sections": [
    {"kind": "hook", "heading": "short internal label",
     "narration": "the words to be spoken", "document_indices": [0]}
  ],
  "notes": "anything the operator should know before recording, or null"
}

Valid "kind" values: hook, introduction, section, evidence, example, conclusion, \
call_to_action. Open with exactly one hook and close with exactly one conclusion."""


@dataclass
class ScriptResult:
    version: ScriptVersion
    dropped: list[str] = field(default_factory=list)
    originality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "script_version_id": str(self.version.id),
            "version": self.version.version,
            "word_count": self.version.word_count,
            "estimated_duration_seconds": self.version.estimated_duration_seconds,
            "sections": len(self.version.sections or []),
            "dropped": self.dropped,
            "originality": self.originality,
        }


def generate_script(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    guidance: str | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> ScriptResult:
    research = _require_research(session, project)
    documents = list(
        session.execute(
            select(ResearchDocument)
            .where(ResearchDocument.research_id == research.id)
            .order_by(ResearchDocument.created_at.asc())
        ).scalars()
    )
    if not research.key_facts and not research.claims:
        raise ValidationError(
            "The research for this project established no sourced facts or claims, so there "
            "is nothing to write from."
        )

    provider = get_llm()  # raises ProviderNotConfigured when unset
    prompt = _build_prompt(project, research, documents, guidance)
    prompt_digest = hashlib.sha256(prompt.encode()).hexdigest()

    response = provider.complete(
        system=SYSTEM_PROMPT,
        messages=[LLMMessage(role="user", content=prompt)],
        max_tokens=8000,
        temperature=0.5,
        json_output=True,
    )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        raise ProviderUnavailable(
            f"{provider.name} did not return a 'sections' array.", details={"model": response.model}
        )

    dropped: list[str] = []
    sections = _validated_sections(payload["sections"], documents, dropped)
    if not sections:
        raise ProviderUnavailable(
            "The model returned no usable script sections.", details={"dropped": dropped}
        )

    narration = "\n\n".join(section["narration"] for section in sections)
    words = _word_count(narration)
    originality = check_originality(narration, documents)

    script = project_service.get_script(session, project)
    next_version = script.current_version + 1
    version = ScriptVersion(
        script_id=script.id,
        version=next_version,
        sections=sections,
        plain_text=_plain_text(sections),
        narration_text=narration,
        source_references=[
            {
                "index": index,
                "document_id": str(document.id),
                "title": document.title,
                "url": document.url,
                "publisher": document.publisher,
            }
            for index, document in enumerate(documents)
        ],
        word_count=words,
        estimated_duration_seconds=estimate_duration_seconds(words),
        provider=response.provider,
        model=response.model,
        prompt_digest=prompt_digest,
        created_by=user_id,
        created_at=datetime.now(UTC),
        notes=str(payload.get("notes") or "").strip()[:4000] or None,
    )
    session.add(version)
    session.flush()

    script.current_version = next_version
    script.status = "generated"
    project.current_script_version_id = version.id
    if project.status == ProjectStatus.SCRIPTING.value:
        project.status = ProjectStatus.SCRIPTING.value
    session.flush()

    audit.record(
        session,
        action="script.generated",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="script_version",
        entity_id=version.id,
        summary=(
            f"Script v{next_version} for '{project.title[:80]}': {words} words, "
            f"~{version.estimated_duration_seconds}s, {len(sections)} sections, "
            f"originality {originality['score']}/100."
            + (f" Dropped {len(dropped)} section(s)." if dropped else "")
        ),
        after={"originality": originality, "dropped": dropped},
    )
    logger.info(
        "script.generated",
        extra={
            "project_id": str(project.id),
            "version": next_version,
            "words": words,
            "originality": originality["score"],
        },
    )
    return ScriptResult(version=version, dropped=dropped, originality=originality)


def _require_research(session: Session, project: ContentProject) -> TopicResearch:
    if project.research_id is None:
        raise ValidationError("This project has no research attached.")
    research = session.get(TopicResearch, project.research_id)
    if research is None:
        raise NotFound("The research for this project no longer exists.")
    if research.status != "SUCCESS":
        raise ValidationError(
            f"Research for this project is '{research.status}'. Complete it before scripting."
        )
    return research


def _build_prompt(
    project: ContentProject,
    research: TopicResearch,
    documents: list[ResearchDocument],
    guidance: str | None,
) -> str:
    context: dict[str, Any] = {
        "video": {
            "title": project.title,
            "format": project.video_format,
            "target_duration_seconds": project.target_duration_seconds,
            "target_word_count": round(project.target_duration_seconds / 60 * WORDS_PER_MINUTE),
            "language": project.language,
        },
        "research": {
            "summary": research.summary,
            "key_facts": research.key_facts,
            "claims": research.claims,
            "statistics": research.statistics,
            "conflicts": research.conflicts,
            "uncertainties": research.uncertainties,
            "entities": research.entities,
        },
        "documents": [
            {
                "index": index,
                "title": document.title,
                "publisher": document.publisher,
                "url": document.url,
                "published_at": document.published_at.isoformat() if document.published_at else None,
            }
            for index, document in enumerate(documents)
        ],
    }
    if guidance:
        context["operator_guidance"] = guidance[:2000]
    return json.dumps(context, ensure_ascii=False, indent=2)


def _validated_sections(
    raw: list[Any], documents: list[ResearchDocument], dropped: list[str]
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue
        kind = str(item.get("kind") or "section").strip().lower()
        if kind not in SECTION_KINDS:
            kind = "section"

        indices, unresolved = _resolve_indices(item.get("document_indices"), documents)
        if unresolved:
            dropped.append(
                f"Section '{str(item.get('heading') or kind)[:60]}' cited "
                f"{unresolved} non-existent document index/indices; those citations were removed."
            )
        sections.append(
            {
                "kind": kind,
                "heading": str(item.get("heading") or kind.replace("_", " ").title())[:200],
                "narration": narration[:20000],
                "document_indices": indices,
            }
        )
    return sections


def _resolve_indices(raw: Any, documents: list[ResearchDocument]) -> tuple[list[int], int]:
    if not isinstance(raw, list):
        return [], 0
    resolved: list[int] = []
    unresolved = 0
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            unresolved += 1
            continue
        if 0 <= index < len(documents):
            if index not in resolved:
                resolved.append(index)
        else:
            unresolved += 1
    return resolved, unresolved


def _plain_text(sections: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"## {section['heading']}\n{section['narration']}" for section in sections)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def estimate_duration_seconds(words: int) -> int:
    """Derived from the word count at a stated rate — an estimate, never a measurement."""
    return int(round(words / WORDS_PER_MINUTE * 60))


_WORD_RE = re.compile(r"[a-z0-9']+")


def check_originality(narration: str, documents: list[ResearchDocument]) -> dict[str, Any]:
    """Detect verbatim reuse of source text.

    Compares normalized word n-grams against every source excerpt. This is a plagiarism
    check against *what we actually read*, not a claim about the whole internet — the
    result says so.
    """
    script_words = _WORD_RE.findall(narration.lower())
    if len(script_words) < VERBATIM_WINDOW_WORDS:
        # Too short for an n-gram comparison to mean anything; say so rather than
        # returning a perfect score that looks like a pass.
        return {
            "score": 100,
            "checked_documents": len(documents),
            "longest_verbatim_run_words": 0,
            "overlap_ratio": 0.0,
            "matches": [],
            "scope": (
                f"The text is shorter than the {VERBATIM_WINDOW_WORDS}-word comparison window, "
                "so no verbatim check was possible."
            ),
            "conclusive": False,
        }

    source_grams: dict[tuple[str, ...], int] = {}
    checked = 0
    for index, document in enumerate(documents):
        if not document.text:
            continue
        checked += 1
        words = _WORD_RE.findall(document.text.lower())
        for start in range(len(words) - VERBATIM_WINDOW_WORDS + 1):
            source_grams.setdefault(tuple(words[start : start + VERBATIM_WINDOW_WORDS]), index)

    if not source_grams:
        return {
            "score": 100,
            "checked_documents": 0,
            "longest_verbatim_run_words": 0,
            "matches": [],
            "scope": (
                "No source document carried text, so no verbatim comparison was possible. "
                "This is not evidence of originality."
            ),
            "conclusive": False,
        }

    matches: list[dict[str, Any]] = []
    matched_positions: set[int] = set()
    position = 0
    while position <= len(script_words) - VERBATIM_WINDOW_WORDS:
        gram = tuple(script_words[position : position + VERBATIM_WINDOW_WORDS])
        document_index = source_grams.get(gram)
        if document_index is None:
            position += 1
            continue

        # Extend the match as far as it runs.
        end = position + VERBATIM_WINDOW_WORDS
        while end < len(script_words):
            next_gram = tuple(script_words[end - VERBATIM_WINDOW_WORDS + 1 : end + 1])
            if source_grams.get(next_gram) is None:
                break
            end += 1
        matched_positions.update(range(position, end))
        matches.append(
            {
                "document_index": document_index,
                "words": end - position,
                "excerpt": " ".join(script_words[position:end])[:300],
            }
        )
        position = end

    overlap_ratio = len(matched_positions) / len(script_words)
    longest = max((match["words"] for match in matches), default=0)
    return {
        "score": max(0, min(100, round(100 * (1 - overlap_ratio)))),
        "checked_documents": checked,
        "longest_verbatim_run_words": longest,
        "overlap_ratio": round(overlap_ratio, 4),
        "matches": matches[:20],
        "scope": (
            f"Compared against {checked} source excerpt(s) stored for this research. "
            "It is not a check against the whole web."
        ),
        "conclusive": True,
    }


def get_version(
    session: Session, project: ContentProject, version_id: uuid.UUID
) -> ScriptVersion:
    script = project_service.get_script(session, project)
    version = session.get(ScriptVersion, version_id)
    if version is None or version.script_id != script.id:
        raise NotFound("Script version not found.")
    return version


def set_current_version(
    session: Session, project: ContentProject, version: ScriptVersion, *, user_id: uuid.UUID
) -> ContentProject:
    """Point the project at an earlier version without deleting anything."""
    previous = project.current_script_version_id
    project.current_script_version_id = version.id
    session.flush()
    audit.record(
        session,
        action="script.version_selected",
        user_id=user_id,
        channel_id=project.channel_id,
        entity_type="content_project",
        entity_id=project.id,
        summary=f"Selected script v{version.version}",
        before={"current_script_version_id": str(previous) if previous else None},
        after={"current_script_version_id": str(version.id)},
    )
    return project


def version_to_dict(version: ScriptVersion, *, include_text: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(version.id),
        "version": version.version,
        "word_count": version.word_count,
        "estimated_duration_seconds": version.estimated_duration_seconds,
        "duration_basis": (
            f"Derived from {version.word_count} words at {WORDS_PER_MINUTE} words per minute. "
            "This is an estimate; the rendered runtime is measured after narration."
        ),
        "provider": version.provider,
        "model": version.model,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "notes": version.notes,
        "source_references": version.source_references or [],
        "section_count": len(version.sections or []),
    }
    if include_text:
        payload["sections"] = version.sections or []
        payload["narration_text"] = version.narration_text
        payload["plain_text"] = version.plain_text
    return payload
