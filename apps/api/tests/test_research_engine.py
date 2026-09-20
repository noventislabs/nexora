"""Research engine: grounding, classification, conflict preservation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import Conflict, ProviderNotConfigured, ValidationError
from nexora.db.models import (
    AuditLog,
    Channel,
    ResearchDocument,
    TopicCandidate,
    TopicResearch,
    TrendingTopic,
)
from nexora.services.channels import get_channel_settings
from nexora.services.research.engine import run_research
from nexora.services.trends import sources as source_service
from tests.fixtures import feeds

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "fixture-key")
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-5")


def anthropic_reply(payload: dict) -> httpx.Response:
    text = json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "id": "msg_fixture",
            "model": "claude-sonnet-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text[1:]}],
            "usage": {"input_tokens": 2000, "output_tokens": 800},
        },
    )


@pytest.fixture
def candidate(db: Session, channel: Channel) -> TopicCandidate:
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Fixture Feed",
        config={"url": "https://fixture-news.invalid/feed.xml", "category": "technology"},
    )
    now = datetime.now(UTC)
    rows = []
    for index, (title, summary) in enumerate(
        [
            (
                "TEST FIXTURE: foundry adds wafer starts",
                "The regional foundry added 40,000 wafer starts per month in the second quarter.",
            ),
            (
                "TEST FIXTURE: association disputes the figure",
                "A second association places the addition nearer 25,000 wafer starts per month.",
            ),
        ]
    ):
        row = TrendingTopic(
            source_id=source.id,
            channel_id=channel.id,
            source_kind="rss",
            source_name="Fixture Feed",
            external_id=f"doc{index}",
            dedupe_hash=f"{index:064d}",
            content_hash=f"{index + 500:064d}",
            title=title,
            url=f"https://fixture-news.invalid/articles/{index}",
            summary=summary,
            category="technology",
            discovered_at=now - timedelta(hours=index),
            published_at=now - timedelta(hours=index + 1),
            signal_score=70,
            signal_breakdown={"score": 70, "competition_level": "low", "components": []},
        )
        db.add(row)
        rows.append(row)
    db.flush()

    row = TopicCandidate(
        channel_id=channel.id,
        trending_topic_id=rows[0].id,
        title="Why the wafer-start numbers disagree",
        angle="Explains why two industry bodies report different additions.",
        category="technology",
        status="approved",
        sources=[
            {
                "trending_topic_id": str(item.id),
                "title": item.title,
                "url": item.url,
                "source_name": item.source_name,
                "source_kind": item.source_kind,
                "published_at": item.published_at.isoformat(),
            }
            for item in rows
        ],
        opportunity_score=70,
    )
    db.add(row)
    db.commit()
    return row


def test_research_without_an_llm_is_not_configured(
    db: Session, channel: Channel, candidate: TopicCandidate, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "")
    with pytest.raises(ProviderNotConfigured):
        run_research(db, channel, candidate)
    assert db.query(TopicResearch).count() == 0
    assert db.query(ResearchDocument).count() == 0


def test_research_requires_cited_evidence(db: Session, channel: Channel, llm) -> None:
    bare = TopicCandidate(
        channel_id=channel.id, title="Ungrounded", angle="No sources.", sources=[], status="approved"
    )
    db.add(bare)
    db.commit()
    with pytest.raises(ValidationError) as exc:
        run_research(db, channel, bare)
    assert "cites no trend items" in exc.value.message


@respx.mock
def test_research_grounds_everything_in_documents(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))

    result = run_research(db, channel, candidate)
    db.commit()

    research = result.research
    assert research.status == "SUCCESS"
    assert research.document_count == 2
    assert db.query(ResearchDocument).count() == 2

    # The uncited "fact" was dropped, the cited claim kept.
    statements = [claim["statement"] for claim in research.claims]
    assert any("40,000 wafer starts" in statement for statement in statements)
    assert not any("competitor will exit" in statement for statement in statements)
    assert any("Unsourced claim" in note for note in result.dropped)

    # The statistic citing a non-existent document index was dropped.
    values = [statistic["value"] for statistic in research.statistics]
    assert "40,000" in values
    assert "83%" not in values
    assert any("Unsourced statistic" in note for note in result.dropped)


@respx.mock
def test_full_text_is_not_fetched_unless_enabled(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    page = respx.get(url__startswith="https://fixture-news.invalid/articles/").mock(
        return_value=httpx.Response(200, text=feeds.SOURCE_PAGE_HTML)
    )
    run_research(db, channel, candidate)
    db.commit()

    assert page.call_count == 0, "pages must not be retrieved while the setting is off"
    documents = db.query(ResearchDocument).all()
    assert {document.fetch_decision for document in documents} == {"DISABLED"}
    # The feed summary is still used as text, so research is still possible.
    assert all(document.text for document in documents)


@respx.mock
def test_full_text_is_fetched_when_enabled_and_robots_permits(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    from nexora.services.research import fetch as fetch_module

    fetch_module.reset_robots_cache()
    settings_row = get_channel_settings(db, channel.id)
    settings_row.research_full_text_enabled = True
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    respx.get("https://fixture-news.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=feeds.ROBOTS_ALLOW_ALL)
    )
    respx.get(url__startswith="https://fixture-news.invalid/articles/").mock(
        return_value=httpx.Response(
            200, text=feeds.SOURCE_PAGE_HTML, headers={"content-type": "text/html"}
        )
    )
    run_research(db, channel, candidate)
    db.commit()

    documents = db.query(ResearchDocument).all()
    assert {document.fetch_decision for document in documents} == {"ALLOWED"}
    assert all(document.word_count and document.word_count > 20 for document in documents)
    assert all(document.checksum_sha256 for document in documents)
    fetch_module.reset_robots_cache()


@respx.mock
def test_robots_refusal_is_recorded_and_research_continues(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    from nexora.services.research import fetch as fetch_module

    fetch_module.reset_robots_cache()
    settings_row = get_channel_settings(db, channel.id)
    settings_row.research_full_text_enabled = True
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    respx.get("https://fixture-news.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text=feeds.ROBOTS_DISALLOW_ALL)
    )
    result = run_research(db, channel, candidate)
    db.commit()

    documents = db.query(ResearchDocument).all()
    assert {document.fetch_decision for document in documents} == {"BLOCKED_BY_ROBOTS"}
    assert all("disallows" in (document.fetch_note or "") for document in documents)
    # Research still ran on the feed summaries rather than failing outright.
    assert result.research.status == "SUCCESS"
    fetch_module.reset_robots_cache()


@respx.mock
def test_conflicts_are_preserved_and_one_sided_ones_dropped(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    result = run_research(db, channel, candidate)

    conflicts = result.research.conflicts
    assert len(conflicts) == 1, "a conflict needs two independently sourced positions"
    assert conflicts[0]["subject"] == "size of the wafer-start addition"
    assert len(conflicts[0]["positions"]) == 2
    assert any("without two sourced positions" in note for note in result.dropped)


@respx.mock
def test_classification_is_demoted_when_unsupported(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    """A FACT the code cannot tie to a document must not remain a FACT."""
    payload = {
        **feeds.RESEARCH_RESPONSE,
        "key_facts": [
            {
                "statement": "Something the documents support.",
                "classification": "FACT",
                "document_indices": [0],
            },
            {
                "statement": "Something invented.",
                "classification": "FACT",
                "document_indices": [42],
            },
        ],
    }
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(payload))
    result = run_research(db, channel, candidate)

    facts = result.research.key_facts
    assert len(facts) == 1
    assert facts[0]["classification"] == "FACT"
    assert any("Unsourced key fact" in note for note in result.dropped)


@respx.mock
def test_invalid_classification_falls_back_to_unknown(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    payload = {
        **feeds.RESEARCH_RESPONSE,
        "key_facts": [
            {"statement": "A statement.", "classification": "DEFINITELY_TRUE", "document_indices": [0]}
        ],
    }
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(payload))
    result = run_research(db, channel, candidate)
    assert result.research.key_facts[0]["classification"] == "UNKNOWN"


@respx.mock
def test_rerunning_requires_force_and_replaces_documents(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    first = run_research(db, channel, candidate)
    db.commit()

    with pytest.raises(Conflict):
        run_research(db, channel, candidate)

    second = run_research(db, channel, candidate, force=True)
    db.commit()
    assert second.research.id == first.research.id
    assert db.query(ResearchDocument).count() == 2, "documents are replaced, not duplicated"


@respx.mock
def test_provider_failure_marks_research_failed(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    from nexora.core.errors import ProviderUnavailable

    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    with pytest.raises(ProviderUnavailable):
        run_research(db, channel, candidate)

    # The failure is recorded on the research row rather than leaving it "RUNNING".
    db.commit()
    research = db.query(TopicResearch).one()
    assert research.status == "FAILED"
    assert research.error
    assert not research.key_facts


@respx.mock
def test_research_is_audited(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    run_research(db, channel, candidate)
    db.commit()
    entry = db.query(AuditLog).filter(AuditLog.action == "research.completed").one()
    assert "2 document(s)" in (entry.summary or "")


@respx.mock
def test_the_prompt_forbids_outside_knowledge(
    db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    route = respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    run_research(db, channel, candidate)

    sent = json.loads(route.calls[0].request.content)
    assert "Never state a fact that is not in the documents" in sent["system"]
    assert "Do NOT pick a winner" in sent["system"]
    assert "40,000 wafer starts" in sent["messages"][0]["content"]
