"""Script versioning, originality checking and fact-check adjudication."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import ProviderNotConfigured, ValidationError
from nexora.db.models import (
    Channel,
    ContentProject,
    FactCheck,
    ResearchDocument,
    ScriptVersion,
    TopicCandidate,
    TopicResearch,
    TrendingTopic,
)
from nexora.db.models.enums import CheckStatus, ProjectStatus
from nexora.services import factcheck as factcheck_service
from nexora.services import projects as project_service
from nexora.services import scripts as script_service
from nexora.services.channels import get_channel_settings
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
            "content": [{"type": "text", "text": text[1:]}],
            "usage": {"input_tokens": 1500, "output_tokens": 900},
        },
    )


@pytest.fixture
def researched(db: Session, channel: Channel) -> tuple[TopicCandidate, TopicResearch]:
    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Fixture Feed",
        config={"url": "https://fixture-news.invalid/feed.xml"},
    )
    now = datetime.now(UTC)
    trend = TrendingTopic(
        source_id=source.id,
        channel_id=channel.id,
        source_kind="rss",
        source_name="Fixture Feed",
        external_id="d0",
        dedupe_hash="a" * 64,
        content_hash="b" * 64,
        title="TEST FIXTURE: foundry capacity",
        url="https://fixture-news.invalid/articles/0",
        summary="Packaging capacity remains the binding constraint on output.",
        discovered_at=now,
        published_at=now - timedelta(hours=2),
    )
    db.add(trend)
    db.flush()

    candidate = TopicCandidate(
        channel_id=channel.id,
        title="Why wafer-start numbers disagree",
        angle="Explains the dispute.",
        status="approved",
        sources=[{"trending_topic_id": str(trend.id), "title": trend.title, "url": trend.url,
                  "source_name": "Fixture Feed", "source_kind": "rss", "published_at": None}],
    )
    db.add(candidate)
    db.flush()

    research = TopicResearch(
        topic_candidate_id=candidate.id,
        channel_id=channel.id,
        status="SUCCESS",
        summary="Two bodies disagree on the figure.",
        key_facts=[
            {
                "statement": "Packaging is the binding constraint.",
                "classification": "FACT",
                "document_indices": [0],
            }
        ],
        claims=[
            {
                "statement": "The addition was 40,000 wafer starts.",
                "classification": "CLAIM",
                "attributed_to": "the industry association",
                "document_indices": [0],
            }
        ],
        statistics=[],
        conflicts=[
            {
                "subject": "size of the wafer start addition",
                "positions": [
                    {"position": "about 40,000", "document_indices": [0]},
                    {"position": "nearer 25,000", "document_indices": [1]},
                ],
            }
        ],
        uncertainties=["Whether packaging expands next quarter."],
        document_count=2,
        provider="anthropic",
        model="claude-sonnet-5",
        finished_at=now,
    )
    db.add(research)
    db.flush()

    for index, text in enumerate(
        [
            "The regional foundry added 40,000 wafer starts per month in the second quarter, "
            "according to the industry association.",
            "A second association places the addition nearer 25,000 wafer starts per month.",
        ]
    ):
        db.add(
            ResearchDocument(
                research_id=research.id,
                channel_id=channel.id,
                trending_topic_id=trend.id if index == 0 else None,
                origin="trend_item",
                url=f"https://fixture-news.invalid/articles/{index}",
                title=f"TEST FIXTURE document {index}",
                publisher="Fixture Feed",
                text=text,
                word_count=len(text.split()),
                fetch_decision="DISABLED",
                created_at=now,
            )
        )
    db.commit()
    return candidate, research


@pytest.fixture
def project(db: Session, channel: Channel, researched) -> ContentProject:
    candidate, _ = researched
    project = project_service.create_from_candidate(db, channel, candidate, user_id=None)
    db.commit()
    return project


# -------------------------------------------------------------------------- projects
def test_project_requires_an_approved_candidate(db: Session, channel: Channel, researched) -> None:
    candidate, _ = researched
    candidate.status = "proposed"
    db.commit()
    with pytest.raises(ValidationError) as exc:
        project_service.create_from_candidate(db, channel, candidate)
    assert "approved or saved" in exc.value.message


def test_project_requires_research(db: Session, channel: Channel) -> None:
    bare = TopicCandidate(
        channel_id=channel.id, title="No research", angle="None.", status="approved", sources=[]
    )
    db.add(bare)
    db.commit()
    with pytest.raises(ValidationError) as exc:
        project_service.create_from_candidate(db, channel, bare)
    assert "Research this candidate first" in exc.value.message


def test_project_enforces_the_minimum_document_threshold(
    db: Session, channel: Channel, researched
) -> None:
    candidate, research = researched
    settings_row = get_channel_settings(db, channel.id)
    settings_row.research_min_documents = 5
    db.commit()
    with pytest.raises(ValidationError) as exc:
        project_service.create_from_candidate(db, channel, candidate)
    assert "below the channel minimum" in exc.value.message


def test_project_creation_converts_the_candidate(db: Session, channel: Channel, project) -> None:
    assert project.status == ProjectStatus.SCRIPTING.value
    assert project.approval_status == "pending"
    candidate = db.get(TopicCandidate, project.topic_candidate_id)
    assert candidate.status == "converted"


def test_short_format_duration_is_bounded(db: Session, channel: Channel, researched) -> None:
    candidate, _ = researched
    with pytest.raises(ValidationError):
        project_service.create_from_candidate(
            db, channel, candidate, video_format="short", target_duration_seconds=600
        )


# --------------------------------------------------------------------------- scripts
def test_script_generation_without_an_llm_is_not_configured(
    db: Session, channel: Channel, project, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "")
    with pytest.raises(ProviderNotConfigured):
        script_service.generate_script(db, channel, project)
    assert db.query(ScriptVersion).count() == 0


@respx.mock
def test_script_generation_creates_an_immutable_version(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    result = script_service.generate_script(db, channel, project, user_id=None)
    db.commit()

    version = result.version
    assert version.version == 1
    assert len(version.sections) == 3
    assert version.word_count > 0
    assert version.estimated_duration_seconds == script_service.estimate_duration_seconds(
        version.word_count
    )
    assert version.narration_text.startswith("Two industry bodies")
    assert project.current_script_version_id == version.id

    # The section citing document 99 had that citation stripped and reported.
    conclusion = next(s for s in version.sections if s["kind"] == "conclusion")
    assert conclusion["document_indices"] == [0]
    assert any("non-existent document index" in note for note in result.dropped)


@respx.mock
def test_regenerating_adds_a_version_and_keeps_the_old_one(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    first = script_service.generate_script(db, channel, project)
    db.commit()
    second = script_service.generate_script(db, channel, project)
    db.commit()

    assert first.version.version == 1
    assert second.version.version == 2
    assert db.query(ScriptVersion).count() == 2
    assert db.get(ScriptVersion, first.version.id) is not None, "history is never overwritten"
    assert project.current_script_version_id == second.version.id


@respx.mock
def test_an_earlier_version_can_be_reselected(
    db: Session, channel: Channel, project, llm, user
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    first = script_service.generate_script(db, channel, project)
    script_service.generate_script(db, channel, project)
    db.commit()

    script_service.set_current_version(db, project, first.version, user_id=user.id)
    db.commit()
    assert project.current_script_version_id == first.version.id


@respx.mock
def test_originality_is_measured_against_the_stored_sources(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    result = script_service.generate_script(db, channel, project)

    assert result.originality["conclusive"] is True
    assert result.originality["checked_documents"] == 2
    assert result.originality["score"] == 100
    assert "not a check against the whole web" in result.originality["scope"]


@respx.mock
def test_a_copied_script_scores_badly(db: Session, channel: Channel, project, llm) -> None:
    copied = {
        "sections": [
            {
                "kind": "hook",
                "heading": "Copied",
                "narration": "The regional foundry added 40,000 wafer starts per month in the "
                "second quarter, according to the industry association.",
                "document_indices": [0],
            }
        ],
        "notes": None,
    }
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(copied))
    result = script_service.generate_script(db, channel, project)

    assert result.originality["score"] < 30
    assert result.originality["longest_verbatim_run_words"] >= 9
    assert result.originality["matches"]


@respx.mock
def test_a_script_with_no_usable_sections_is_an_error(
    db: Session, channel: Channel, project, llm
) -> None:
    from nexora.core.errors import ProviderUnavailable

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply({"sections": [], "notes": None}))
    with pytest.raises(ProviderUnavailable):
        script_service.generate_script(db, channel, project)


# ------------------------------------------------------------------------ fact check
@respx.mock
def test_fact_check_adjudicates_in_code_not_by_the_model(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    version = script_service.generate_script(db, channel, project).version
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_RESPONSE))
    result = factcheck_service.run_fact_check(db, channel, project, version)
    db.commit()

    verdicts = {claim["assertion"][:30]: claim["verdict"] for claim in result.claims}
    assert verdicts["Packaging is the binding const"] == "SUPPORTED"
    # The research recorded disagreement about this figure and the script states it as
    # settled. Sources actively disagreeing is a stronger finding than loose wording,
    # so it is CONTRADICTED rather than merely overstated.
    assert verdicts["The addition was exactly 40,00"] == "CONTRADICTED"
    # No citation -> unverified, regardless of how confident the model sounded. The
    # research exists; nothing in it supports this.
    assert verdicts["A competitor will exit the mar"] == "UNVERIFIED"

    assert result.check.status == CheckStatus.FAIL.value
    assert result.check.supported_count == 1
    # Both blocking verdicts are counted together for the roll-up.
    assert result.check.unsupported_count == 2
    assert factcheck_service.check_to_dict(result.check)["blocks_publishing"] is True

    counts = factcheck_service.verdict_counts(result.check)
    assert counts == {
        "SUPPORTED": 1,
        "PARTIALLY_SUPPORTED": 0,
        "CONTRADICTED": 1,
        "UNVERIFIED": 1,
        "INSUFFICIENT_SOURCES": 0,
    }
    assert len(factcheck_service.blocking_claims(result.check)) == 2


@respx.mock
def test_a_clean_check_passes(db: Session, channel: Channel, project, llm) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    version = script_service.generate_script(db, channel, project).version
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_ALL_SUPPORTED))
    result = factcheck_service.run_fact_check(db, channel, project, version)
    assert result.check.status == CheckStatus.PASS.value
    assert factcheck_service.check_to_dict(result.check)["blocks_publishing"] is False


@respx.mock
def test_citations_to_documents_without_text_do_not_count(
    db: Session, channel: Channel, project, llm
) -> None:
    for document in db.query(ResearchDocument).all():
        document.text = None
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    version = script_service.generate_script(db, channel, project).version
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_ALL_SUPPORTED))
    result = factcheck_service.run_fact_check(db, channel, project, version)

    # With no document carrying text, nothing was checkable at all. That is
    # INSUFFICIENT_SOURCES rather than UNVERIFIED: the two need different fixes —
    # one is a gap in the research, the other a claim the research contradicts.
    assert result.claims[0]["verdict"] == "INSUFFICIENT_SOURCES"
    assert result.check.status == CheckStatus.FAIL.value


def test_no_extracted_claims_is_review_not_pass() -> None:
    """Extracting nothing means unverified, which is not the same as verified."""
    assert factcheck_service.determine_status(
        supported=0, needs_review=0, unsupported=0, claims=0
    ) is CheckStatus.REVIEW


@respx.mock
def test_contradictions_carry_the_research_conflicts_forward(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    version = script_service.generate_script(db, channel, project).version
    db.commit()

    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_RESPONSE))
    result = factcheck_service.run_fact_check(db, channel, project, version)

    kinds = {item["kind"] for item in result.check.contradictions}
    assert "source_conflict" in kinds
    assert "unsupported_assertion" in kinds
    assert "unverified_specific" in kinds


@respx.mock
def test_fact_check_is_persisted_and_retrievable(
    db: Session, channel: Channel, project, llm
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    version = script_service.generate_script(db, channel, project).version
    db.commit()
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_ALL_SUPPORTED))
    factcheck_service.run_fact_check(db, channel, project, version)
    db.commit()

    assert db.query(FactCheck).count() == 1
    latest = factcheck_service.latest_for_project(db, project.id)
    assert latest is not None and latest.script_version_id == version.id
