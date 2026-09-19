"""Research engine.

The model synthesises; the code decides what counts as evidence.

* Documents are assembled first, from stored trend items and (only where permitted)
  their linked pages. The model sees exactly those documents and nothing else.
* Every extracted fact, claim and statistic must cite document indices. Citations are
  resolved against the assembled documents; anything unresolvable is **dropped**, not
  stored as unsourced text.
* Classification is constrained to FACT / CLAIM / ANALYSIS / OPINION / UNKNOWN, and a
  statement the code cannot tie to a document is forced to UNKNOWN regardless of what
  the model labelled it.
* Conflicting sources are preserved as conflicts. They are never reconciled away.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, NotFound, ProviderUnavailable, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import Channel, ResearchDocument, TopicCandidate, TopicResearch, TrendingTopic
from nexora.db.models.enums import ActorType, ClaimClassification
from nexora.services import audit
from nexora.services.providers.llm import LLMMessage, get_llm
from nexora.services.research import fetch as fetch_module

logger = get_logger(__name__)

MAX_DOCUMENTS = 12
MAX_DOCUMENT_CHARS_IN_PROMPT = 6_000

VALID_CLASSIFICATIONS = {member.value for member in ClaimClassification}

SYSTEM_PROMPT = """You are a research analyst preparing evidence for a factual video script.

You are given SOURCE DOCUMENTS that were actually retrieved. Work only from them.

Hard rules:
- Never state a fact that is not in the documents. You have no other knowledge to draw on \
for this task.
- Cite the document indices that support every fact, claim and statistic.
- Classify each statement honestly:
  FACT     - directly stated in a document and not disputed by another document
  CLAIM    - asserted by a source but not independently corroborated here
  ANALYSIS - an inference you are drawing from the documents
  OPINION  - someone's judgement or preference, attributed to whoever holds it
  UNKNOWN  - relevant but the documents do not settle it
- If documents disagree, record the disagreement in "conflicts". Do NOT pick a winner \
and do NOT average them.
- Record what a viewer would reasonably want to know that the documents do not answer, \
in "uncertainties".
- Copy no sentence verbatim from a document. Summarise in your own words.
- Do not estimate audience numbers, revenue or performance of any kind.

Return ONLY a JSON object of this exact shape:
{
  "summary": "what the evidence collectively establishes, in 3-6 sentences",
  "key_facts": [
    {"statement": "...", "classification": "FACT", "document_indices": [0, 2]}
  ],
  "claims": [
    {"statement": "...", "classification": "CLAIM", "attributed_to": "who asserts it, or null",
     "document_indices": [1]}
  ],
  "statistics": [
    {"value": "42%", "what_it_measures": "...", "as_of": "2026-03 or null",
     "document_indices": [0]}
  ],
  "entities": {"organizations": ["..."], "people": ["..."], "places": ["..."]},
  "dates": [{"date": "2026-03-14", "what_happened": "...", "document_indices": [2]}],
  "conflicts": [
    {"subject": "...", "positions": [
       {"position": "...", "document_indices": [0]},
       {"position": "...", "document_indices": [3]}]}
  ],
  "uncertainties": ["what the documents do not establish"]
}"""


@dataclass
class ResearchResult:
    research: TopicResearch
    documents: list[ResearchDocument]
    dropped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_id": str(self.research.id),
            "status": self.research.status,
            "document_count": self.research.document_count,
            "key_facts": len(self.research.key_facts or []),
            "claims": len(self.research.claims or []),
            "conflicts": len(self.research.conflicts or []),
            "dropped": self.dropped,
        }


# --------------------------------------------------------------------- documents
def assemble_documents(
    session: Session,
    channel: Channel,
    candidate: TopicCandidate,
    *,
    allow_full_text: bool,
) -> list[dict[str, Any]]:
    """Build the document set for a candidate from its cited trend evidence."""
    trend_ids: list[uuid.UUID] = []
    for reference in candidate.sources or []:
        raw = reference.get("trending_topic_id")
        if not raw:
            continue
        try:
            trend_ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue

    if not trend_ids:
        raise ValidationError(
            "This candidate cites no trend items, so there is nothing to research."
        )

    rows = list(
        session.execute(
            select(TrendingTopic).where(
                TrendingTopic.id.in_(trend_ids), TrendingTopic.channel_id == channel.id
            )
        ).scalars()
    )
    # Include corroborating rows: a second source on the same story is extra evidence.
    originals = [row.id for row in rows if row.duplicate_of_id is None]
    if originals:
        rows.extend(
            session.execute(
                select(TrendingTopic).where(TrendingTopic.duplicate_of_id.in_(originals))
            ).scalars()
        )

    documents: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for row in rows[:MAX_DOCUMENTS]:
        if row.id in seen:
            continue
        seen.add(row.id)
        documents.append(_document_from_trend(row, allow_full_text=allow_full_text))
    return documents


def _document_from_trend(row: TrendingTopic, *, allow_full_text: bool) -> dict[str, Any]:
    document: dict[str, Any] = {
        "origin": "trend_item",
        "trending_topic_id": row.id,
        "url": row.url,
        "title": row.title,
        "publisher": row.source_name,
        "author": row.author,
        "published_at": row.published_at,
        "text": row.summary,
        "fetch_decision": fetch_module.NOT_ATTEMPTED,
        "fetch_note": "Only the title and summary supplied by the source were used.",
        "truncated": False,
        "http_status": None,
        "content_type": None,
        "checksum": None,
        "fetched_at": None,
        "error": None,
    }
    if not row.url:
        document["fetch_note"] = "This source item carries no link, so no page could be retrieved."
        return document

    result = fetch_module.fetch_document(row.url, enabled=allow_full_text)
    document["fetch_decision"] = result.decision
    document["fetch_note"] = result.note
    document["error"] = result.error
    document["http_status"] = result.http_status
    document["content_type"] = result.content_type
    document["fetched_at"] = result.fetched_at
    document["checksum"] = result.checksum
    if result.succeeded:
        document["text"] = result.text
        document["truncated"] = result.truncated
        if result.title:
            document["title"] = result.title
    return document


def _persist_documents(
    session: Session, research: TopicResearch, channel: Channel, documents: list[dict[str, Any]]
) -> list[ResearchDocument]:
    rows: list[ResearchDocument] = []
    now = datetime.now(UTC)
    for document in documents:
        row = ResearchDocument(
            research_id=research.id,
            channel_id=channel.id,
            trending_topic_id=document.get("trending_topic_id"),
            origin=document["origin"],
            url=document.get("url"),
            title=document["title"][:2000],
            publisher=document.get("publisher"),
            author=document.get("author"),
            published_at=document.get("published_at"),
            text=document.get("text"),
            word_count=fetch_module.word_count(document.get("text")),
            truncated=bool(document.get("truncated")),
            fetched_at=document.get("fetched_at"),
            http_status=document.get("http_status"),
            content_type=document.get("content_type"),
            checksum_sha256=document.get("checksum"),
            fetch_decision=document["fetch_decision"],
            fetch_note=document.get("fetch_note"),
            error=document.get("error"),
            created_at=now,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


# ----------------------------------------------------------------------- research
def run_research(
    session: Session,
    channel: Channel,
    candidate: TopicCandidate,
    *,
    actor_type: ActorType = ActorType.USER,
    user_id: uuid.UUID | None = None,
    force: bool = False,
) -> ResearchResult:
    """Research a topic candidate against its cited evidence."""
    existing = session.execute(
        select(TopicResearch).where(TopicResearch.topic_candidate_id == candidate.id)
    ).scalar_one_or_none()
    if existing is not None and existing.status == "SUCCESS" and not force:
        raise Conflict(
            "This candidate has already been researched. Pass force=true to research it again."
        )

    from nexora.services.channels import get_channel_settings

    settings_row = get_channel_settings(session, channel.id)
    documents = assemble_documents(
        session, channel, candidate, allow_full_text=settings_row.research_full_text_enabled
    )
    usable = [document for document in documents if (document.get("text") or "").strip()]
    if len(usable) < 1:
        raise ValidationError(
            "None of this candidate's sources provided any usable text, so there is nothing "
            "to research. Enable full-text research for this channel, or choose a candidate "
            "whose sources include summaries."
        )

    provider = get_llm()  # raises ProviderNotConfigured when unset

    research = existing or TopicResearch(
        topic_candidate_id=candidate.id, channel_id=channel.id, status="RUNNING"
    )
    research.status = "RUNNING"
    research.started_at = datetime.now(UTC)
    research.error = None
    session.add(research)
    session.flush()

    # Replace any documents from a previous run so the record matches this run exactly.
    for stale in session.execute(
        select(ResearchDocument).where(ResearchDocument.research_id == research.id)
    ).scalars():
        session.delete(stale)
    session.flush()
    document_rows = _persist_documents(session, research, channel, documents)

    try:
        response = provider.complete(
            system=SYSTEM_PROMPT,
            messages=[LLMMessage(role="user", content=_build_prompt(candidate, documents))],
            max_tokens=6000,
            temperature=0.1,
            json_output=True,
        )
        payload = response.json()
    except Exception as exc:
        research.status = "FAILED"
        research.finished_at = datetime.now(UTC)
        research.error = str(exc)[:4000]
        research.document_count = len(document_rows)
        session.flush()
        raise

    if not isinstance(payload, dict):
        research.status = "FAILED"
        research.finished_at = datetime.now(UTC)
        research.error = "The provider did not return a research object."
        session.flush()
        raise ProviderUnavailable(f"{provider.name} did not return a research object.")

    dropped: list[str] = []
    research.summary = str(payload.get("summary") or "").strip() or None
    research.key_facts = _validated_statements(
        payload.get("key_facts"), documents, dropped, label="key fact"
    )
    research.claims = _validated_statements(
        payload.get("claims"), documents, dropped, label="claim"
    )
    research.statistics = _validated_statistics(payload.get("statistics"), documents, dropped)
    research.entities = _validated_entities(payload.get("entities"))
    research.conflicts = _validated_conflicts(payload.get("conflicts"), documents, dropped)
    research.uncertainties = [
        str(item).strip()[:1000] for item in payload.get("uncertainties", []) if str(item).strip()
    ][:25]
    research.sources = [
        {
            "index": index,
            "document_id": str(row.id),
            "title": row.title,
            "url": row.url,
            "publisher": row.publisher,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "fetch_decision": row.fetch_decision,
        }
        for index, row in enumerate(document_rows)
    ]
    research.document_count = len(document_rows)
    research.provider = response.provider
    research.model = response.model
    research.status = "SUCCESS"
    research.finished_at = datetime.now(UTC)
    session.flush()

    audit.record(
        session,
        action="research.completed",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="topic_research",
        entity_id=research.id,
        summary=(
            f"Researched '{candidate.title[:80]}' over {len(document_rows)} document(s): "
            f"{len(research.key_facts)} fact(s), {len(research.claims)} claim(s), "
            f"{len(research.conflicts)} conflict(s)."
            + (f" Dropped {len(dropped)} unsourced statement(s)." if dropped else "")
        ),
        after={"dropped": dropped, "provider": response.provider, "model": response.model},
    )
    logger.info(
        "research.completed",
        extra={
            "research_id": str(research.id),
            "documents": len(document_rows),
            "dropped": len(dropped),
        },
    )
    return ResearchResult(research=research, documents=document_rows, dropped=dropped)


def _build_prompt(candidate: TopicCandidate, documents: list[dict[str, Any]]) -> str:
    rendered = []
    for index, document in enumerate(documents):
        text = (document.get("text") or "").strip()
        entry: dict[str, Any] = {
            "index": index,
            "title": document["title"],
            "publisher": document.get("publisher"),
            "url": document.get("url"),
            "published_at": (
                document["published_at"].isoformat() if document.get("published_at") else None
            ),
            "availability": document["fetch_note"],
        }
        if text:
            entry["text"] = text[:MAX_DOCUMENT_CHARS_IN_PROMPT]
            if len(text) > MAX_DOCUMENT_CHARS_IN_PROMPT:
                entry["text_truncated"] = True
        else:
            entry["text"] = None
            entry["note"] = "No text was available for this document; do not cite it for facts."
        rendered.append(entry)

    return (
        "TOPIC UNDER RESEARCH\n"
        + json.dumps(
            {"title": candidate.title, "angle": candidate.angle, "why_now": candidate.why_now},
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nSOURCE DOCUMENTS\n"
        + json.dumps(rendered, ensure_ascii=False, indent=2)
    )


# ---------------------------------------------------------------------- validation
def _resolve_indices(raw: Any, documents: list[dict[str, Any]]) -> list[int]:
    """Keep only indices that point at a document that actually carried text."""
    if not isinstance(raw, list):
        return []
    resolved: list[int] = []
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(documents) and index not in resolved:
            if (documents[index].get("text") or "").strip():
                resolved.append(index)
    return resolved


def _classification(raw: Any, *, supported: bool) -> str:
    """Constrain the label, and demote anything the code cannot tie to a document."""
    value = str(raw or "").strip().upper()
    if value not in VALID_CLASSIFICATIONS:
        value = ClaimClassification.UNKNOWN.value
    if not supported and value in (ClaimClassification.FACT.value, ClaimClassification.CLAIM.value):
        # A "fact" with no resolvable source is not a fact we can stand behind.
        return ClaimClassification.UNKNOWN.value
    return value


def _validated_statements(
    raw: Any, documents: list[dict[str, Any]], dropped: list[str], *, label: str
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or "").strip()
        if not statement:
            continue
        indices = _resolve_indices(item.get("document_indices"), documents)
        if not indices:
            dropped.append(f"Unsourced {label}: {statement[:120]}")
            continue
        entry: dict[str, Any] = {
            "statement": statement[:2000],
            "classification": _classification(item.get("classification"), supported=True),
            "document_indices": indices,
        }
        attributed = str(item.get("attributed_to") or "").strip()
        if attributed and attributed.lower() != "null":
            entry["attributed_to"] = attributed[:255]
        out.append(entry)
    return out


def _validated_statistics(
    raw: Any, documents: list[dict[str, Any]], dropped: list[str]
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        measures = str(item.get("what_it_measures") or "").strip()
        if not value or not measures:
            continue
        indices = _resolve_indices(item.get("document_indices"), documents)
        if not indices:
            # An uncited number is the single most dangerous thing to keep.
            dropped.append(f"Unsourced statistic: {value} ({measures[:80]})")
            continue
        as_of = str(item.get("as_of") or "").strip()
        out.append(
            {
                "value": value[:120],
                "what_it_measures": measures[:500],
                "as_of": as_of[:64] if as_of and as_of.lower() != "null" else None,
                "document_indices": indices,
            }
        )
    return out


def _validated_entities(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key in ("organizations", "people", "places"):
        values = raw.get(key)
        if isinstance(values, list):
            out[key] = [str(value).strip()[:160] for value in values if str(value).strip()][:40]
    return out


def _validated_conflicts(
    raw: Any, documents: list[dict[str, Any]], dropped: list[str]
) -> list[dict[str, Any]]:
    """A conflict needs at least two independently cited positions to be meaningful."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        positions_raw = item.get("positions")
        if not subject or not isinstance(positions_raw, list):
            continue
        positions = []
        for position in positions_raw:
            if not isinstance(position, dict):
                continue
            text = str(position.get("position") or "").strip()
            indices = _resolve_indices(position.get("document_indices"), documents)
            if text and indices:
                positions.append({"position": text[:1000], "document_indices": indices})
        if len(positions) < 2:
            dropped.append(f"Conflict without two sourced positions: {subject[:120]}")
            continue
        out.append({"subject": subject[:500], "positions": positions})
    return out


def get_research(session: Session, channel_id: uuid.UUID, research_id: uuid.UUID) -> TopicResearch:
    research = session.get(TopicResearch, research_id)
    if research is None or research.channel_id != channel_id:
        raise NotFound("Research not found.")
    return research
