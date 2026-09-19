"""Topic candidate generation.

The LLM is intercepted at the HTTP boundary (respx) so the real adapter code runs.
The generator itself is tested for the property that matters: nothing a model claims
is accepted unless it can be checked against stored evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import NotFound, ProviderNotConfigured, ProviderUnavailable
from nexora.db.models import AuditLog, Channel, TopicCandidate, TrendingTopic
from nexora.db.models.enums import CandidateStatus
from nexora.services.topics import generate_candidates, select_evidence
from nexora.services.trends import sources as source_service

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "fixture-key")
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-5")


@pytest.fixture
def evidence(db: Session, channel: Channel) -> list[TrendingTopic]:
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Fixture Feed",
        config={"url": "https://fixture.invalid/feed.xml", "category": "technology"},
    )
    now = datetime.now(UTC)
    rows = []
    for index, title in enumerate(
        [
            "TEST FIXTURE: semiconductor fabrication capacity expands",
            "TEST FIXTURE: AI model training costs analysed",
            "TEST FIXTURE: grid storage economics shift",
        ]
    ):
        row = TrendingTopic(
            source_id=source.id,
            channel_id=channel.id,
            source_kind="rss",
            source_name="Fixture Feed",
            external_id=f"e{index}",
            dedupe_hash=f"{index:064d}",
            content_hash=f"{index + 100:064d}",
            title=title,
            url=f"https://fixture.invalid/{index}",
            summary="Synthetic summary.",
            category="technology",
            discovered_at=now - timedelta(hours=index),
            published_at=now - timedelta(hours=index + 1),
            opportunity_score=80 - index * 10,
            score_breakdown={"score": 80 - index * 10, "competition_level": "low", "components": []},
            scored_at=now,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def anthropic_reply(payload: dict) -> httpx.Response:
    """Shape a real Anthropic Messages response around a fixture body."""
    text = json.dumps(payload)
    # The adapter prefills "{" for JSON mode, so the model returns the remainder.
    return httpx.Response(
        200,
        json={
            "id": "msg_fixture",
            "model": "claude-sonnet-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text[1:]}],
            "usage": {"input_tokens": 1200, "output_tokens": 400},
        },
    )


def test_generation_without_a_provider_raises_not_configured(
    db: Session, channel: Channel, evidence, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "")
    with pytest.raises(ProviderNotConfigured) as exc:
        generate_candidates(db, channel, count=3)
    assert "LLM_PROVIDER" in str(exc.value.details)
    assert db.query(TopicCandidate).count() == 0


def test_generation_without_evidence_raises_not_found(db: Session, channel: Channel, llm) -> None:
    with pytest.raises(NotFound):
        generate_candidates(db, channel, count=3)


@respx.mock
def test_generation_materializes_cited_candidates(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {
                "candidates": [
                    {
                        "title": "Why fab capacity decides the next AI cycle",
                        "angle": "Connects fabrication capacity to model training cost.",
                        "audience": "Technically literate business viewers",
                        "category": "technology",
                        "why_now": "Two collected items touch the same constraint.",
                        "evidence_indices": [0, 1],
                        "risks": ["Capacity figures change quickly"],
                    }
                ]
            }
        )
    )
    result = generate_candidates(db, channel, count=3)
    db.commit()

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.title == "Why fab capacity decides the next AI cycle"
    assert candidate.status == CandidateStatus.PROPOSED.value
    assert candidate.generated_by_provider == "anthropic"
    assert candidate.evidence_count == 2
    assert candidate.evidence_source_kinds == ["rss"]
    assert len(candidate.sources) == 2
    assert candidate.sources[0]["url"] == "https://fixture.invalid/0"
    # Freshness comes from the evidence, not from generation time.
    assert candidate.newest_evidence_at is not None
    assert candidate.oldest_evidence_at is not None
    assert candidate.oldest_evidence_at <= candidate.newest_evidence_at


@respx.mock
def test_score_is_inherited_from_evidence_never_asked_of_the_model(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {
                "candidates": [
                    {
                        "title": "A topic",
                        "angle": "An angle.",
                        "evidence_indices": [1, 2],
                        # A model-supplied score must be ignored entirely.
                        "opportunity_score": 99,
                        "estimated_views": 500000,
                    }
                ]
            }
        )
    )
    result = generate_candidates(db, channel, count=1)
    candidate = result.candidates[0]

    # Evidence 1 scores 70 and evidence 2 scores 60 — the best cited item wins.
    assert candidate.opportunity_score == 70
    assert candidate.score_breakdown["derived_from"]["cited_items_scored"] == 2
    assert "never asked to produce a score" in candidate.score_breakdown["derived_from"]["rule"]
    assert 99 not in (candidate.score_breakdown or {}).values()


@respx.mock
def test_a_candidate_citing_nothing_is_dropped(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {
                "candidates": [
                    {"title": "Grounded", "angle": "Real.", "evidence_indices": [0]},
                    {"title": "Ungrounded invention", "angle": "Made up.", "evidence_indices": []},
                    {"title": "Out of range", "angle": "Cites nothing real.", "evidence_indices": [99]},
                ]
            }
        )
    )
    result = generate_candidates(db, channel, count=5)
    db.commit()

    assert [candidate.title for candidate in result.candidates] == ["Grounded"]
    assert len(result.dropped) == 2
    assert any("Ungrounded invention" in note for note in result.dropped)
    assert db.query(TopicCandidate).count() == 1


@respx.mock
def test_a_category_the_channel_does_not_have_is_not_accepted(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {
                "candidates": [
                    {
                        "title": "A topic",
                        "angle": "An angle.",
                        "category": "cryptocurrency-pump-signals",
                        "evidence_indices": [0],
                    }
                ]
            }
        )
    )
    candidate = generate_candidates(db, channel, count=1).candidates[0]
    assert candidate.category in channel.categories


@respx.mock
def test_unscored_evidence_yields_an_unavailable_score(
    db: Session, channel: Channel, evidence, llm
) -> None:
    for row in evidence:
        row.opportunity_score = None
        row.score_breakdown = None
    db.commit()

    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {"candidates": [{"title": "A topic", "angle": "An angle.", "evidence_indices": [0]}]}
        )
    )
    candidate = generate_candidates(db, channel, count=1).candidates[0]
    assert candidate.opportunity_score is None
    assert candidate.score_breakdown["available"] is False
    assert "no Opportunity Score" in candidate.score_breakdown["unavailable_reason"]


@respx.mock
def test_malformed_model_output_is_an_error_not_a_guess(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_fixture",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "I cannot produce JSON today."}],
                "usage": {},
            },
        )
    )
    with pytest.raises(ProviderUnavailable):
        generate_candidates(db, channel, count=1)
    assert db.query(TopicCandidate).count() == 0


@respx.mock
def test_response_without_a_candidates_array_is_rejected(
    db: Session, channel: Channel, evidence, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply({"topics": []}))
    with pytest.raises(ProviderUnavailable) as exc:
        generate_candidates(db, channel, count=1)
    assert "candidates" in exc.value.message


@respx.mock
def test_llm_rate_limit_propagates(db: Session, channel: Channel, evidence, llm) -> None:
    from nexora.core.errors import RateLimited

    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limit"}})
    )
    with pytest.raises(RateLimited):
        generate_candidates(db, channel, count=1)


@respx.mock
def test_llm_timeout_propagates(db: Session, channel: Channel, evidence, llm) -> None:
    respx.post(ANTHROPIC_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailable):
        generate_candidates(db, channel, count=1)


@respx.mock
def test_generation_is_audited(db: Session, channel: Channel, evidence, llm) -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {"candidates": [{"title": "A topic", "angle": "An angle.", "evidence_indices": [0]}]}
        )
    )
    generate_candidates(db, channel, count=1)
    db.commit()

    entry = db.query(AuditLog).filter(AuditLog.action == "topics.generated").one()
    assert "anthropic" in (entry.summary or "")


@respx.mock
def test_the_prompt_carries_only_real_collected_items(
    db: Session, channel: Channel, evidence, llm
) -> None:
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {"candidates": [{"title": "A topic", "angle": "An angle.", "evidence_indices": [0]}]}
        )
    )
    generate_candidates(db, channel, count=2)

    sent = json.loads(route.calls[0].request.content)
    prompt = sent["messages"][0]["content"]
    assert "TEST FIXTURE: semiconductor fabrication capacity expands" in prompt
    assert "Never invent a fact" in sent["system"]
    assert "Do not estimate views" in sent["system"]
    # The API key travels in the header, never in the body.
    assert "fixture-key" not in prompt
    assert route.calls[0].request.headers["x-api-key"] == "fixture-key"


@respx.mock
def test_openai_adapter_is_interchangeable(
    db: Session, channel: Channel, evidence, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "fixture-openai-key")
    monkeypatch.setattr(settings, "openai_model", "gpt-4.1")

    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "title": "An OpenAI-sourced topic",
                                            "angle": "An angle.",
                                            "evidence_indices": [0],
                                        }
                                    ]
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 120},
            },
        )
    )
    result = generate_candidates(db, channel, count=1)
    assert result.provider == "openai"
    assert result.candidates[0].generated_by_model == "gpt-4.1"
    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"}


def test_select_evidence_excludes_duplicates(db: Session, channel: Channel, evidence) -> None:
    evidence[2].duplicate_of_id = evidence[0].id
    db.commit()
    selected = select_evidence(db, channel.id)
    assert evidence[2] not in selected
    assert len(selected) == 2


def test_select_evidence_orders_by_score(db: Session, channel: Channel, evidence) -> None:
    selected = select_evidence(db, channel.id)
    scores = [row.opportunity_score for row in selected]
    assert scores == sorted(scores, reverse=True)
