"""Topic candidate generation.

The LLM proposes an editorial angle. Everything checkable is computed here in code,
not accepted from the model:

* every candidate must cite trend rows that exist, by index — unresolvable citations
  are dropped rather than kept as bare text;
* ``opportunity_score`` is inherited from the cited evidence, never asked for;
* freshness is derived from the evidence timestamps.

With no LLM configured this raises ``ProviderNotConfigured``. There is no offline
"idea generator" that would produce plausible-looking topics from nothing.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ProviderUnavailable, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import (
    Channel,
    ChannelTopicRelevance,
    TopicCandidate,
    TrendingTopic,
)
from nexora.db.models.enums import ActorType, CandidateStatus, RelevanceStatus
from nexora.services import audit
from nexora.services.providers.llm import LLMMessage, get_llm

logger = get_logger(__name__)

MAX_CANDIDATES = 12
DEFAULT_CANDIDATES = 6
EVIDENCE_LOOKBACK = timedelta(days=7)

SYSTEM_PROMPT = """You are an editorial researcher for a YouTube channel.

You are given TREND ITEMS that were actually collected from the channel's configured \
sources. Propose video topics that are supported by those items.

You are also given the CHANNEL's own profile: its audience, categories, brand voice and the topics it refuses to cover. The same trend items are shown to channels with very different audiences, so a topic that suits one channel may be wrong for this one.

Hard rules:
- Ground every topic in the supplied items. Cite them by their numeric index.
- Write for THIS channel's stated audience. If an item cannot be turned into something appropriate for that audience, skip it rather than forcing it.
- Never propose a topic touching anything in the channel's "must_not_cover" list.
- Do not assume facts about the audience beyond what the profile states. If the profile is thin, propose fewer topics rather than inventing a viewer persona.
- Never invent a fact, statistic, quotation, date or source that is not in the items.
- If the items do not support a topic, propose fewer topics. Returning fewer good \
topics is correct; padding the list is not.
- Do not estimate views, revenue, subscriber gains or virality. Do not output any score.
- Prefer topics that give a viewer genuine explanatory value over reaction or \
aggregation formats.
- Flag real risks: unverified claims, contested facts, legal/medical/financial \
sensitivity, or reliance on third-party footage.

Return ONLY a JSON object of this exact shape:
{
  "candidates": [
    {
      "title": "concrete, specific working title (max 100 chars)",
      "angle": "what this video argues or explains, and why it is distinct",
      "audience": "who specifically this is for",
      "category": "one of the channel's categories",
      "why_now": "what in the cited items makes this timely, or 'evergreen' if it is not time-bound",
      "evidence_indices": [0, 3],
      "risks": ["specific risk", "..."]
    }
  ]
}"""


@dataclass
class GenerationResult:
    candidates: list[TopicCandidate]
    run_id: uuid.UUID
    provider: str
    model: str
    evidence_considered: int
    dropped: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "provider": self.provider,
            "model": self.model,
            "evidence_considered": self.evidence_considered,
            "generated": len(self.candidates),
            "dropped": self.dropped,
        }


def select_evidence(
    session: Session,
    channel: Channel,
    *,
    limit: int = 25,
    min_score: int | None = None,
    trend_ids: list[uuid.UUID] | None = None,
) -> list[TrendingTopic]:
    """The trend rows a generation run will reason over, ranked **for this channel**.

    Two rules matter here:

    * Evidence is ordered by this channel's relevance score, not by the trend row's
      channel-independent signal. A story that is big everywhere but irrelevant to
      this channel should not be the material a script is built from.
    * A row this channel has excluded is never passed to the model. Offering the
      operator's forbidden topic as evidence, even at the bottom of the list, would
      quietly ignore a rule they set.
    """
    visible = (TrendingTopic.channel_id == channel.id) | (
        TrendingTopic.channel_id.is_(None) & (TrendingTopic.user_id == channel.user_id)
    )
    stmt = (
        select(TrendingTopic, ChannelTopicRelevance)
        .outerjoin(
            ChannelTopicRelevance,
            (ChannelTopicRelevance.trending_topic_id == TrendingTopic.id)
            & (ChannelTopicRelevance.channel_id == channel.id),
        )
        .where(
            visible,
            TrendingTopic.duplicate_of_id.is_(None),
            ChannelTopicRelevance.status.is_distinct_from(RelevanceStatus.EXCLUDED.value),
        )
    )
    if trend_ids:
        stmt = stmt.where(TrendingTopic.id.in_(trend_ids))
    else:
        stmt = stmt.where(TrendingTopic.discovered_at >= datetime.now(UTC) - EVIDENCE_LOOKBACK)
        if min_score is not None:
            stmt = stmt.where(ChannelTopicRelevance.score >= min_score)
    stmt = stmt.order_by(
        ChannelTopicRelevance.score.desc().nullslast(),
        TrendingTopic.signal_score.desc().nullslast(),
        TrendingTopic.discovered_at.desc(),
    ).limit(limit)
    return [row for row, _ in session.execute(stmt).all()]


def channel_score(
    session: Session, channel_id: uuid.UUID, topic_id: uuid.UUID
) -> ChannelTopicRelevance | None:
    return session.execute(
        select(ChannelTopicRelevance).where(
            ChannelTopicRelevance.channel_id == channel_id,
            ChannelTopicRelevance.trending_topic_id == topic_id,
        )
    ).scalar_one_or_none()


def generate_candidates(
    session: Session,
    channel: Channel,
    *,
    count: int = DEFAULT_CANDIDATES,
    min_score: int | None = None,
    trend_ids: list[uuid.UUID] | None = None,
    actor_type: ActorType = ActorType.USER,
    user_id: uuid.UUID | None = None,
) -> GenerationResult:
    if not 1 <= count <= MAX_CANDIDATES:
        raise ValidationError(f"Requested candidate count must be between 1 and {MAX_CANDIDATES}.")

    evidence = select_evidence(session, channel, min_score=min_score, trend_ids=trend_ids)
    if not evidence:
        raise NotFound(
            "There are no collected trend items to build topics from. Run a trend scan first."
        )

    provider = get_llm()  # raises ProviderNotConfigured when unset
    from nexora.services.profiles import get_profile

    profile = get_profile(session, channel.id)
    relevance = {
        record.trending_topic_id: record
        for record in session.execute(
            select(ChannelTopicRelevance).where(
                ChannelTopicRelevance.channel_id == channel.id,
                ChannelTopicRelevance.trending_topic_id.in_([row.id for row in evidence]),
            )
        ).scalars()
    }
    prompt = _build_prompt(channel, evidence, count, profile=profile, relevance=relevance)
    response = provider.complete(
        system=SYSTEM_PROMPT,
        messages=[LLMMessage(role="user", content=prompt)],
        max_tokens=4096,
        temperature=0.4,
        json_output=True,
    )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ProviderUnavailable(
            f"{provider.name} did not return a 'candidates' array.",
            details={"model": response.model},
        )

    run_id = uuid.uuid4()
    created: list[TopicCandidate] = []
    dropped: list[str] = []

    for raw in payload["candidates"][:count]:
        candidate = _materialize(
            session, channel, raw, evidence, run_id=run_id, response=response, dropped=dropped
        )
        if candidate is not None:
            created.append(candidate)

    session.flush()
    audit.record(
        session,
        action="topics.generated",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="topic_generation_run",
        entity_id=run_id,
        summary=(
            f"Generated {len(created)} candidate(s) from {len(evidence)} trend item(s) "
            f"using {provider.name}/{response.model}."
            + (f" Dropped {len(dropped)}." if dropped else "")
        ),
        after={"dropped": dropped, "provider": provider.name, "model": response.model},
    )
    logger.info(
        "topics.generated",
        extra={
            "channel_id": str(channel.id),
            "run_id": str(run_id),
            "created": len(created),
            "dropped": len(dropped),
        },
    )
    return GenerationResult(
        candidates=created,
        run_id=run_id,
        provider=provider.name,
        model=response.model,
        evidence_considered=len(evidence),
        dropped=dropped,
    )


def _build_prompt(
    channel: Channel,
    evidence: list[TrendingTopic],
    count: int,
    *,
    profile: Any = None,
    relevance: dict[uuid.UUID, ChannelTopicRelevance] | None = None,
) -> str:
    items = []
    for index, row in enumerate(evidence):
        item: dict[str, Any] = {
            "index": index,
            "title": row.title,
            "source": row.source_name,
            "source_kind": row.source_kind,
            "url": row.url,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
            "corroborating_sources": row.corroboration_count,
        }
        if row.summary:
            item["summary"] = row.summary[:600]
        if row.engagement:
            item["engagement"] = row.engagement
        if row.category:
            item["category"] = row.category
        record = (relevance or {}).get(row.id)
        if record is not None:
            # Why *this channel* was shown this item, so the model proposes topics that
            # fit the channel rather than topics that merely fit the news.
            item["relevance_to_this_channel"] = {
                "status": record.status,
                "matched_categories": [
                    entry.get("category") for entry in (record.matched_categories or [])
                ],
                "matched_preferences": [
                    entry.get("preference") for entry in (record.matched_preferences or [])
                ],
            }
        items.append(item)

    channel_context: dict[str, Any] = {
        "name": channel.name,
        "description": channel.description,
        "primary_categories": list(channel.categories or []),
        "primary_language": channel.primary_language,
    }
    if profile is not None:
        # The channel's own configuration, so a kids channel and a finance channel get
        # visibly different instructions from the same evidence pool.
        channel_context.update(
            {
                "audience_description": profile.audience_description,
                "audience_classification": profile.audience_classification,
                "secondary_categories": list(profile.secondary_categories or []),
                "brand_voice": profile.brand_voice,
                "preferred_topics": list(profile.preferred_topics or []),
                "must_not_cover": sorted(
                    set(profile.blocked_topics or [])
                    | set(profile.content_exclusions or [])
                    | set(profile.sensitive_content_restrictions or [])
                ),
                "short_form_enabled": profile.short_form_enabled,
                "long_form_enabled": profile.long_form_enabled,
            }
        )

    context = {
        "channel": channel_context,
        "requested_candidates": count,
        "trend_items": items,
    }
    return (
        "Channel context and collected trend items:\n\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + f"\n\nPropose at most {count} topics. Cite only the indices above."
    )


def _materialize(
    session: Session,
    channel: Channel,
    raw: Any,
    evidence: list[TrendingTopic],
    *,
    run_id: uuid.UUID,
    response: Any,
    dropped: list[str],
) -> TopicCandidate | None:
    if not isinstance(raw, dict):
        dropped.append("A candidate was not an object.")
        return None

    title = str(raw.get("title") or "").strip()
    angle = str(raw.get("angle") or "").strip()
    if not title or not angle:
        dropped.append(f"Candidate '{title[:60] or '(untitled)'}' had no title or no angle.")
        return None

    cited = _resolve_evidence(raw.get("evidence_indices"), evidence)
    if not cited:
        # A topic that cites nothing is exactly the kind of invention this system refuses.
        dropped.append(f"Candidate '{title[:60]}' cited no resolvable trend item.")
        return None

    inherited = _inherit_score(session, channel.id, cited)
    published = [row.published_at for row in cited if row.published_at]
    discovered = [row.discovered_at for row in cited if row.discovered_at]
    timestamps = published or discovered

    candidate = TopicCandidate(
        channel_id=channel.id,
        trending_topic_id=cited[0].id,
        generation_run_id=run_id,
        title=title[:300],
        angle=angle,
        audience=str(raw.get("audience") or "").strip() or None,
        category=_validated_category(raw.get("category"), channel, cited),
        why_now=str(raw.get("why_now") or "").strip() or None,
        risks=[str(risk)[:300] for risk in raw.get("risks", []) if str(risk).strip()][:10],
        sources=[
            {
                "trending_topic_id": str(row.id),
                "title": row.title,
                "url": row.url,
                "source_name": row.source_name,
                "source_kind": row.source_kind,
                "published_at": row.published_at.isoformat() if row.published_at else None,
            }
            for row in cited
        ],
        opportunity_score=inherited["score"],
        score_breakdown=inherited["breakdown"],
        competition_level=inherited["competition_level"],
        evidence_count=len(cited),
        evidence_source_kinds=sorted({row.source_kind for row in cited}),
        newest_evidence_at=max(timestamps) if timestamps else None,
        oldest_evidence_at=min(timestamps) if timestamps else None,
        status=CandidateStatus.PROPOSED.value,
        generated_by_provider=response.provider,
        generated_by_model=response.model,
    )
    session.add(candidate)
    return candidate


def _resolve_evidence(indices: Any, evidence: list[TrendingTopic]) -> list[TrendingTopic]:
    if not isinstance(indices, list):
        return []
    resolved: list[TrendingTopic] = []
    for value in indices:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(evidence) and evidence[index] not in resolved:
            resolved.append(evidence[index])
    return resolved


def _validated_category(value: Any, channel: Channel, cited: list[TrendingTopic]) -> str | None:
    """Keep the model's category only if it is one the channel actually configured."""
    categories = list(channel.categories or [])
    proposed = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if proposed in categories:
        return proposed
    for row in cited:
        if row.category and row.category in categories:
            return row.category
    return categories[0] if categories else None


def _inherit_score(
    session: Session, channel_id: uuid.UUID, cited: list[TrendingTopic]
) -> dict[str, Any]:
    """Derive the candidate's score from its evidence rather than asking the model.

    The score inherited is this **channel's** Opportunity Score for the cited item,
    read from its relevance row — not the trend's channel-independent signal. A topic
    is as workable as its strongest support *for the channel that would publish it*.

    When no cited item has been ranked for this channel, the candidate's score is
    unavailable too, and says so.
    """
    relevances = {
        record.trending_topic_id: record
        for record in session.execute(
            select(ChannelTopicRelevance).where(
                ChannelTopicRelevance.channel_id == channel_id,
                ChannelTopicRelevance.trending_topic_id.in_([row.id for row in cited]),
            )
        ).scalars()
    }
    scored = [
        (row, relevances[row.id])
        for row in cited
        if row.id in relevances and relevances[row.id].score is not None
    ]
    if not scored:
        return {
            "score": None,
            "competition_level": "unknown",
            "breakdown": {
                "score": None,
                "available": False,
                "unavailable_reason": (
                    "None of the cited trend items has been ranked for this channel, so "
                    "this candidate has no Opportunity Score."
                ),
                "derived_from": [str(row.id) for row in cited],
            },
        }

    best_row, best_relevance = max(scored, key=lambda pair: pair[1].score or 0)
    signal = dict(best_row.signal_breakdown or {})
    breakdown = {
        **(best_relevance.score_breakdown or {}),
        "signal_components": signal.get("components", []),
        "competition_level": signal.get("competition_level", "unknown"),
        "relevance_status": best_relevance.status,
        "matched_categories": list(best_relevance.matched_categories or []),
        "derived_from": {
            "trending_topic_id": str(best_row.id),
            "title": best_row.title,
            "rule": (
                "Inherited from the cited trend item this channel ranked highest. The "
                "model is never asked to produce a score."
            ),
            "cited_items_scored": len(scored),
            "cited_items_total": len(cited),
        },
    }
    return {
        "score": best_relevance.score,
        "competition_level": breakdown.get("competition_level", "unknown"),
        "breakdown": breakdown,
    }


def decide(
    session: Session,
    candidate: TopicCandidate,
    *,
    status: CandidateStatus,
    user_id: uuid.UUID,
    note: str | None = None,
) -> TopicCandidate:
    candidate.status = status.value
    candidate.decided_at = datetime.now(UTC)
    candidate.decided_by = user_id
    candidate.decision_note = note
    session.flush()
    audit.record(
        session,
        action=f"topic.{status.value}",
        user_id=user_id,
        channel_id=candidate.channel_id,
        entity_type="topic_candidate",
        entity_id=candidate.id,
        summary=f"{status.value}: {candidate.title[:120]}",
    )
    return candidate
