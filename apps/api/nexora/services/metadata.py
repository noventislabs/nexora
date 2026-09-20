"""YouTube metadata: generation and hard validation.

The model drafts a title, description and tags. The *limits* are enforced here against
YouTube's documented constraints, because a draft that exceeds them is rejected by the
API at upload time — far too late.

Source attribution is appended by code, not requested from the model, so it always
matches the research the script was actually written from.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ProviderUnavailable, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import (
    Channel,
    ContentProject,
    MetadataVersion,
    ResearchDocument,
    ScriptVersion,
)
from nexora.db.models.enums import ActorType
from nexora.services import audit
from nexora.services.providers.llm import LLMMessage, get_llm

logger = get_logger(__name__)

#: YouTube's documented limits.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
MAX_TAGS_TOTAL_CHARS = 500
MAX_TAG_CHARS = 30
#: Characters YouTube rejects outright in a title or description.
FORBIDDEN_CHARACTERS = ("<", ">")

SYSTEM_PROMPT = """You are writing YouTube metadata for a factual video.

You are given the SCRIPT and the RESEARCH it was written from.

Hard rules:
- The title must describe what the video actually contains. No clickbait, no \
withheld payoff, no ALL CAPS, no manufactured urgency, no "you won't believe".
- Never promise something the script does not deliver.
- Never state a fact in the description that is not in the script or research.
- Do not write hashtags beyond three, and do not pad tags with unrelated terms.
- Do not mention subscriber counts, view goals, or ask viewers to "smash" anything.
- Write a description a reader would find useful on its own: what the video covers \
and what the evidence shows.

Return ONLY a JSON object of this exact shape:
{
  "title": "at most 100 characters, specific and accurate",
  "description": "2-5 short paragraphs",
  "tags": ["specific", "relevant", "terms"]
}"""


@dataclass
class MetadataResult:
    version: MetadataVersion
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata_version_id": str(self.version.id),
            "version": self.version.version,
            "title": self.version.title,
            "title_length": len(self.version.title),
            "description_length": len(self.version.description),
            "tags": self.version.tags,
            "warnings": self.warnings,
        }


def validate_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise ValidationError("A YouTube title is required.")
    if len(cleaned) > MAX_TITLE_CHARS:
        raise ValidationError(
            f"YouTube titles are limited to {MAX_TITLE_CHARS} characters; this one is "
            f"{len(cleaned)}."
        )
    for character in FORBIDDEN_CHARACTERS:
        if character in cleaned:
            raise ValidationError(f"YouTube rejects '{character}' in a title.")
    return cleaned


def validate_description(description: str) -> str:
    cleaned = (description or "").strip()
    if len(cleaned) > MAX_DESCRIPTION_CHARS:
        raise ValidationError(
            f"YouTube descriptions are limited to {MAX_DESCRIPTION_CHARS} characters; this "
            f"one is {len(cleaned)}."
        )
    for character in FORBIDDEN_CHARACTERS:
        if character in cleaned:
            raise ValidationError(f"YouTube rejects '{character}' in a description.")
    return cleaned


def validate_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Trim tags to YouTube's 500-character total, reporting what was dropped."""
    warnings: list[str] = []
    cleaned: list[str] = []
    total = 0
    for raw in tags or []:
        tag = str(raw).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_CHARS:
            warnings.append(f"Tag '{tag[:20]}…' exceeds {MAX_TAG_CHARS} characters and was dropped.")
            continue
        # YouTube counts a quoted tag's length plus a separator.
        cost = len(tag) + 1
        if total + cost > MAX_TAGS_TOTAL_CHARS:
            warnings.append(
                f"Tags were trimmed at {len(cleaned)} to stay within YouTube's "
                f"{MAX_TAGS_TOTAL_CHARS}-character limit."
            )
            break
        cleaned.append(tag)
        total += cost
    return cleaned, warnings


def build_source_section(documents: list[ResearchDocument]) -> str:
    """Attribution written by code, so it always matches the real research."""
    lines = [line for line in (_source_line(document) for document in documents) if line]
    if not lines:
        return ""
    return "\n\nSources used in this video:\n" + "\n".join(lines)


def _source_line(document: ResearchDocument) -> str | None:
    title = (document.title or "").strip()
    if not title:
        return None
    publisher = f" — {document.publisher}" if document.publisher else ""
    url = f"\n  {document.url}" if document.url else ""
    return f"• {title}{publisher}{url}"


def generate_metadata(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> MetadataResult:
    if project.current_script_version_id is None:
        raise ValidationError("This project has no script to write metadata for.")
    version = session.get(ScriptVersion, project.current_script_version_id)
    if version is None:
        raise NotFound("The current script version no longer exists.")

    documents = (
        list(
            session.execute(
                select(ResearchDocument)
                .where(ResearchDocument.research_id == project.research_id)
                .order_by(ResearchDocument.created_at.asc())
            ).scalars()
        )
        if project.research_id
        else []
    )

    provider = get_llm()  # raises ProviderNotConfigured when unset
    response = provider.complete(
        system=SYSTEM_PROMPT,
        messages=[
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "working_title": project.title,
                        "language": project.language,
                        "video_format": project.video_format,
                        "narration": (version.narration_text or "")[:12000],
                        "sources": [
                            {"title": document.title, "publisher": document.publisher}
                            for document in documents
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        ],
        max_tokens=2000,
        temperature=0.4,
        json_output=True,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderUnavailable(f"{provider.name} did not return a metadata object.")

    warnings: list[str] = []
    title = _fit_title(str(payload.get("title") or project.title), warnings)
    tags, tag_warnings = validate_tags(payload.get("tags") or [])
    warnings.extend(tag_warnings)

    description = str(payload.get("description") or "").strip()
    attribution = build_source_section(documents)
    if attribution and len(description) + len(attribution) <= MAX_DESCRIPTION_CHARS:
        description += attribution
    elif attribution:
        warnings.append(
            "The source list did not fit inside YouTube's description limit and was omitted."
        )
    description = validate_description(description)

    from nexora.services.channels import get_channel_settings

    settings_row = get_channel_settings(session, channel.id)
    next_version = (
        session.execute(
            select(func.coalesce(func.max(MetadataVersion.version), 0)).where(
                MetadataVersion.content_project_id == project.id
            )
        ).scalar_one()
        + 1
    )

    record = MetadataVersion(
        content_project_id=project.id,
        version=next_version,
        title=title,
        description=description,
        tags=tags,
        category_id=settings_row.youtube_category_id,
        default_language=project.language,
        # Mirrors the channel's declaration; publishing is blocked while it is undecided.
        made_for_kids=bool(settings_row.made_for_kids_default),
        provider=response.provider,
        model=response.model,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    project.current_metadata_version_id = record.id
    session.flush()

    audit.record(
        session,
        action="metadata.generated",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="metadata_version",
        entity_id=record.id,
        summary=(
            f"Metadata v{next_version}: {len(title)}-char title, "
            f"{len(description)}-char description, {len(tags)} tag(s)."
        ),
    )
    logger.info(
        "metadata.generated",
        extra={"project_id": str(project.id), "version": next_version, "tags": len(tags)},
    )
    return MetadataResult(version=record, warnings=warnings)


def _fit_title(title: str, warnings: list[str]) -> str:
    cleaned = title.strip()
    for character in FORBIDDEN_CHARACTERS:
        cleaned = cleaned.replace(character, "")
    if len(cleaned) > MAX_TITLE_CHARS:
        # Trim on a word boundary rather than mid-word.
        trimmed = cleaned[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,.;:-–—")
        warnings.append(
            f"The generated title was {len(cleaned)} characters and was trimmed to "
            f"{len(trimmed)} to fit YouTube's {MAX_TITLE_CHARS}-character limit."
        )
        cleaned = trimmed
    return validate_title(cleaned)


def latest_metadata(session: Session, project_id: uuid.UUID) -> MetadataVersion | None:
    return session.execute(
        select(MetadataVersion)
        .where(MetadataVersion.content_project_id == project_id)
        .order_by(MetadataVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def metadata_to_dict(record: MetadataVersion) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "version": record.version,
        "title": record.title,
        "title_length": len(record.title),
        "description": record.description,
        "description_length": len(record.description),
        "tags": record.tags or [],
        "tags_total_chars": sum(len(tag) + 1 for tag in record.tags or []),
        "category_id": record.category_id,
        "default_language": record.default_language,
        "made_for_kids": record.made_for_kids,
        "provider": record.provider,
        "model": record.model,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "limits": {
            "title": MAX_TITLE_CHARS,
            "description": MAX_DESCRIPTION_CHARS,
            "tags_total_chars": MAX_TAGS_TOTAL_CHARS,
        },
    }
