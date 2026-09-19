"""Fact check.

Runs against a specific script version and the research it was written from.

The model extracts the checkable claims and proposes which research documents support
each one. The *verdict* is then computed here, not taken from the model:

* A claim is SUPPORTED only when it cites at least one document that actually exists
  and actually carried text.
* A claim whose citations do not resolve is UNSUPPORTED, whatever the model called it.
* A claim touching a subject the research recorded as a conflict is forced to
  NEEDS_REVIEW — a script must not quietly resolve a disagreement the sources have.
* A date or figure that appears in the narration but in no source document is flagged.

Status: FAIL if anything is unsupported, REVIEW if anything needs review, else PASS.
A FAIL never publishes automatically.
"""

from __future__ import annotations

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
    FactCheck,
    ResearchDocument,
    ScriptVersion,
    TopicResearch,
)
from nexora.db.models.enums import ActorType, CheckStatus
from nexora.services import audit
from nexora.services.providers.llm import LLMMessage, get_llm

logger = get_logger(__name__)

SUPPORTED = "SUPPORTED"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNSUPPORTED = "UNSUPPORTED"

SYSTEM_PROMPT = """You are fact-checking a video narration against the research it was \
written from.

You are given the NARRATION and the RESEARCH (facts, claims, statistics, conflicts, \
uncertainties) with numbered source documents.

Your job is extraction and matching, not judgement of truth in general:
- Extract every checkable factual assertion the narration makes. Skip opinion, framing \
and transitions.
- For each assertion, list the research document indices that support it. If nothing in \
the research supports it, return an empty list. Do NOT guess an index.
- Quote the sentence from the narration that carries the assertion.
- Note any assertion that states something more strongly than the research does \
(for example, the research says a company "plans to" and the narration says it "will").
- Note any date, quantity or proper name in the narration that does not appear in the \
research.

Return ONLY a JSON object of this exact shape:
{
  "claims": [
    {"assertion": "...", "narration_sentence": "...", "document_indices": [0, 2],
     "overstated": false, "note": "optional short note or null"}
  ],
  "unverified_specifics": ["a date, figure or name not present in the research"]
}"""


@dataclass
class FactCheckResult:
    check: FactCheck
    claims: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_check_id": str(self.check.id),
            "status": self.check.status,
            "supported": self.check.supported_count,
            "needs_review": self.check.needs_review_count,
            "unsupported": self.check.unsupported_count,
            "contradictions": len(self.check.contradictions or []),
        }


def run_fact_check(
    session: Session,
    channel: Channel,
    project: ContentProject,
    version: ScriptVersion,
    *,
    actor_type: ActorType = ActorType.USER,
    user_id: uuid.UUID | None = None,
) -> FactCheckResult:
    research = _require_research(session, project)
    documents = list(
        session.execute(
            select(ResearchDocument)
            .where(ResearchDocument.research_id == research.id)
            .order_by(ResearchDocument.created_at.asc())
        ).scalars()
    )
    if not version.narration_text.strip():
        raise ValidationError("This script version has no narration to check.")

    provider = get_llm()  # raises ProviderNotConfigured when unset
    response = provider.complete(
        system=SYSTEM_PROMPT,
        messages=[LLMMessage(role="user", content=_build_prompt(version, research, documents))],
        max_tokens=8000,
        temperature=0.0,
        json_output=True,
    )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise ProviderUnavailable(
            f"{provider.name} did not return a 'claims' array.", details={"model": response.model}
        )

    conflict_terms = _conflict_terms(research)
    claims = [
        adjudicated
        for adjudicated in (
            _adjudicate(raw, documents, conflict_terms) for raw in payload["claims"][:120]
        )
        if adjudicated is not None
    ]
    unverified = [
        str(item).strip()[:300] for item in payload.get("unverified_specifics", []) if str(item).strip()
    ][:40]

    supported = sum(1 for claim in claims if claim["verdict"] == SUPPORTED)
    needs_review = sum(1 for claim in claims if claim["verdict"] == NEEDS_REVIEW)
    unsupported = sum(1 for claim in claims if claim["verdict"] == UNSUPPORTED)
    status = determine_status(
        supported=supported, needs_review=needs_review, unsupported=unsupported, claims=len(claims)
    )

    check = FactCheck(
        content_project_id=project.id,
        script_version_id=version.id,
        status=status.value,
        supported_count=supported,
        needs_review_count=needs_review,
        unsupported_count=unsupported,
        claims=claims,
        contradictions=_contradictions(research, claims, unverified),
        provider=response.provider,
        model=response.model,
        created_at=datetime.now(UTC),
    )
    session.add(check)
    session.flush()

    audit.record(
        session,
        action="fact_check.completed",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="fact_check",
        entity_id=check.id,
        summary=(
            f"Fact check {status.value} for script v{version.version}: "
            f"{supported} supported, {needs_review} need review, {unsupported} unsupported."
        ),
        after={"status": status.value, "unverified_specifics": unverified},
    )
    logger.info(
        "fact_check.completed",
        extra={
            "project_id": str(project.id),
            "status": status.value,
            "supported": supported,
            "needs_review": needs_review,
            "unsupported": unsupported,
        },
    )
    return FactCheckResult(check=check, claims=claims)


def determine_status(
    *, supported: int, needs_review: int, unsupported: int, claims: int
) -> CheckStatus:
    """Any unsupported assertion fails the check. Anything doubtful needs review."""
    if claims == 0:
        # Nothing checkable was extracted. That is not a pass — it is unverified.
        return CheckStatus.REVIEW
    if unsupported > 0:
        return CheckStatus.FAIL
    if needs_review > 0:
        return CheckStatus.REVIEW
    return CheckStatus.PASS


def _require_research(session: Session, project: ContentProject) -> TopicResearch:
    if project.research_id is None:
        raise ValidationError("This project has no research to check against.")
    research = session.get(TopicResearch, project.research_id)
    if research is None:
        raise NotFound("The research for this project no longer exists.")
    return research


def _build_prompt(
    version: ScriptVersion, research: TopicResearch, documents: list[ResearchDocument]
) -> str:
    return json.dumps(
        {
            "narration": version.narration_text,
            "research": {
                "key_facts": research.key_facts,
                "claims": research.claims,
                "statistics": research.statistics,
                "conflicts": research.conflicts,
                "uncertainties": research.uncertainties,
            },
            "documents": [
                {
                    "index": index,
                    "title": document.title,
                    "publisher": document.publisher,
                    "has_text": bool(document.text),
                }
                for index, document in enumerate(documents)
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _adjudicate(
    raw: Any, documents: list[ResearchDocument], conflict_terms: set[str]
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    assertion = str(raw.get("assertion") or "").strip()
    if not assertion:
        return None

    indices, unresolved = _resolve_indices(raw.get("document_indices"), documents)
    reasons: list[str] = []

    if unresolved:
        reasons.append(
            f"{unresolved} cited document index/indices do not exist in this research."
        )
    if not indices:
        verdict = UNSUPPORTED
        reasons.append("No research document supports this assertion.")
    elif raw.get("overstated"):
        verdict = NEEDS_REVIEW
        reasons.append("States the point more strongly than the research does.")
    elif _touches_conflict(assertion, conflict_terms):
        verdict = NEEDS_REVIEW
        reasons.append(
            "Touches a subject the research recorded as contested; a script must not "
            "resolve a disagreement the sources left open."
        )
    else:
        verdict = SUPPORTED

    note = str(raw.get("note") or "").strip()
    if note and note.lower() != "null":
        reasons.append(note[:500])

    return {
        "assertion": assertion[:2000],
        "narration_sentence": str(raw.get("narration_sentence") or "").strip()[:2000] or None,
        "document_indices": indices,
        "verdict": verdict,
        "reasons": reasons,
    }


def _resolve_indices(raw: Any, documents: list[ResearchDocument]) -> tuple[list[int], int]:
    """A citation only counts if the document exists AND carried text to cite."""
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
        if 0 <= index < len(documents) and (documents[index].text or "").strip():
            if index not in resolved:
                resolved.append(index)
        else:
            unresolved += 1
    return resolved, unresolved


_TERM_RE = re.compile(r"[a-z][a-z0-9'-]{3,}")


def _conflict_terms(research: TopicResearch) -> set[str]:
    terms: set[str] = set()
    for conflict in research.conflicts or []:
        subject = str(conflict.get("subject", ""))
        terms.update(_TERM_RE.findall(subject.lower()))
    return terms


def _touches_conflict(assertion: str, conflict_terms: set[str]) -> bool:
    if not conflict_terms:
        return False
    words = set(_TERM_RE.findall(assertion.lower()))
    # Two or more shared distinctive terms, to avoid tripping on a single common word.
    return len(words & conflict_terms) >= 2


def _contradictions(
    research: TopicResearch, claims: list[dict[str, Any]], unverified: list[str]
) -> list[dict[str, Any]]:
    """Carry the research's recorded conflicts forward, plus unverified specifics."""
    out: list[dict[str, Any]] = []
    for conflict in research.conflicts or []:
        out.append(
            {
                "kind": "source_conflict",
                "subject": conflict.get("subject"),
                "positions": conflict.get("positions", []),
                "note": "The sources disagree. The script must present both positions.",
            }
        )
    for item in unverified:
        out.append(
            {
                "kind": "unverified_specific",
                "detail": item,
                "note": "This appears in the narration but not in any research document.",
            }
        )
    for claim in claims:
        if claim["verdict"] == UNSUPPORTED:
            out.append(
                {
                    "kind": "unsupported_assertion",
                    "detail": claim["assertion"],
                    "note": "; ".join(claim["reasons"]),
                }
            )
    return out


def latest_for_project(session: Session, project_id: uuid.UUID) -> FactCheck | None:
    return session.execute(
        select(FactCheck)
        .where(FactCheck.content_project_id == project_id)
        .order_by(FactCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def check_to_dict(check: FactCheck) -> dict[str, Any]:
    return {
        "id": str(check.id),
        "status": check.status,
        "script_version_id": str(check.script_version_id) if check.script_version_id else None,
        "supported": check.supported_count,
        "needs_review": check.needs_review_count,
        "unsupported": check.unsupported_count,
        "claims": check.claims or [],
        "contradictions": check.contradictions or [],
        "provider": check.provider,
        "model": check.model,
        "created_at": check.created_at.isoformat() if check.created_at else None,
        "blocks_publishing": check.status == CheckStatus.FAIL.value,
    }
