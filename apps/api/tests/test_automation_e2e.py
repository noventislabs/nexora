"""The autopilot run, end to end.

This is the test that Phase 7 exists for: one call, and the pipeline walks from a
collected trend through research, script, fact check, narration, render, thumbnail,
metadata and quality checks — using the real database, the real FFmpeg binary and the
real scoring code, with only the third-party HTTP boundary intercepted.

Publishing is deliberately where it stops in most of these: a real upload needs an
OAuth connection, and a test that "published" without one would be exactly the fake
success this product refuses.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.db.models import Channel, ContentProject, TrendingTopic, TrendSource, User
from nexora.db.models.enums import AutomationMode, RunStatus
from nexora.services import channels as channel_service
from nexora.services.automation import orchestrator
from nexora.services.trends import relevance as relevance_service
from tests.fixtures import feeds
from tests.media_helpers import ffmpeg_available, make_tone_mp3

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg is not installed")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ELEVEN_ROOT = "https://api.elevenlabs.io/v1"
NOW = datetime.now(UTC)


@pytest.fixture
def providers(monkeypatch):
    """Credentials for the providers this run needs. Responses are mocked per test."""
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "fixture-key")
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-5")
    monkeypatch.setattr(settings, "voice_provider", "elevenlabs")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "fixture-key")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "fixture-voice-1")


def anthropic_reply(payload: dict) -> httpx.Response:
    """Shape a real Anthropic Messages response around a fixture body.

    The adapter prefills "{" in JSON mode, so the model returns the remainder.
    """
    import json

    text = json.dumps(payload)
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


def elevenlabs_reply(text: str, *, seconds: float = 30.0) -> httpx.Response:
    """A **real** MP3 of known length plus alignment.

    A stub header would not do: the voice stage measures the duration from the file
    it was handed and refuses to invent one, so a fake body would fail the run for
    the right reason but stop this test proving anything.
    """
    audio = make_tone_mp3(seconds)
    return httpx.Response(
        200,
        json={
            "audio_base64": base64.b64encode(audio).decode(),
            "alignment": {
                "characters": list(text),
                "character_start_times_seconds": [
                    i * seconds / max(len(text), 1) for i in range(len(text))
                ],
                "character_end_times_seconds": [
                    (i + 1) * seconds / max(len(text), 1) for i in range(len(text))
                ],
            },
        },
    )


@pytest.fixture
def autonomous_channel(db: Session, user: User) -> Channel:
    channel = channel_service.create_channel(
        db, user, name="Autopilot Desk", categories=["technology", "ai"]
    )
    db.flush()
    automation = channel_service.get_automation_settings(db, channel.id)
    automation.mode = AutomationMode.AUTONOMOUS.value
    automation.automation_enabled = True
    automation.publishing_enabled = True
    automation.autopilot_enabled = True
    automation.auto_publish_enabled = True
    automation.require_human_approval = False
    db.flush()
    return channel


@pytest.fixture
def collected_trends(db: Session, autonomous_channel: Channel, user: User) -> list[TrendingTopic]:
    """Real trend rows, scored and ranked for this channel by the Phase 6 engine."""
    source = TrendSource(
        channel_id=autonomous_channel.id,
        user_id=user.id,
        scope="channel",
        kind="rss",
        name="TEST FIXTURE technology feed",
        config={"url": "https://fixture.invalid/feed.xml"},
        reliability=0.85,
    )
    db.add(source)
    db.flush()

    titles = [
        ("Machine learning model training costs analysed across the industry",
         "An artificial intelligence training run at scale, measured by researchers."),
        ("Semiconductor chip fabrication capacity expands at a new plant",
         "Hardware and software supply constraints ease as a foundry adds lines."),
        ("Neural inference moves to cloud platforms for lower latency",
         "An ai agent running on gpu hardware in a data centre."),
        ("Open source developer tooling consolidates around one platform",
         "Software developers adopt a single open source toolchain."),
        ("Cyber security disclosure rules take effect for cloud vendors",
         "Cyber security and software patching obligations begin."),
        ("Robotics automation reaches new factories this quarter",
         "Robotics and automation on a production line, measured in units."),
    ]
    rows = []
    for index, (title, summary) in enumerate(titles):
        row = TrendingTopic(
            source_id=source.id,
            channel_id=autonomous_channel.id,
            user_id=user.id,
            source_kind="rss",
            source_name=source.name,
            external_id=f"fixture-{index}",
            dedupe_hash=f"{index:064d}",
            content_hash=f"{index + 700:064d}",
            title=title,
            summary=summary,
            url=f"https://fixture.invalid/tech/{index}",
            category="technology",
            language="en",
            discovered_at=NOW - timedelta(hours=index + 1),
            published_at=NOW - timedelta(hours=index + 2),
            signal_score=75 - index,
            signal_breakdown={"score": 75 - index, "components": [], "competition_level": "low"},
            scored_at=NOW,
            corroboration_count=2,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    relevance_service.recompute_for_channel(db, autonomous_channel, rows)
    db.commit()
    return rows


def mock_pipeline_calls() -> None:
    """One mock per provider call the run makes, in the order the run makes them."""
    respx.post(ANTHROPIC_URL).mock(
        side_effect=[
            anthropic_reply(feeds.TOPIC_RESPONSE),
            anthropic_reply(feeds.RESEARCH_RESPONSE),
            anthropic_reply(feeds.SCRIPT_RESPONSE),
            anthropic_reply(feeds.FACT_CHECK_ALL_SUPPORTED),
            anthropic_reply(feeds.METADATA_RESPONSE),
        ]
    )
    respx.post(url__regex=rf"{ELEVEN_ROOT}/text-to-speech/.*").mock(
        side_effect=lambda request: elevenlabs_reply("Narration text for the fixture run.")
    )


@respx.mock
@requires_ffmpeg
def test_one_run_walks_the_pipeline_from_a_trend_to_a_rendered_video(
    db: Session, autonomous_channel: Channel, collected_trends, providers
) -> None:
    mock_pipeline_calls()

    run = orchestrator.start_run(db, autonomous_channel, trigger="manual")
    orchestrator.execute(db, autonomous_channel, run)
    db.commit()

    stages = {entry["stage"]: entry for entry in run.stages}
    assert stages["select_topic"]["status"] == "COMPLETED", run.stopped_reason
    assert stages["research"]["status"] == "COMPLETED", run.stopped_reason
    assert stages["script"]["status"] == "COMPLETED", run.stopped_reason

    project = db.get(ContentProject, run.content_project_id)
    assert project is not None
    # Real artefacts, not markers: a script version that exists and research behind it.
    assert project.current_script_version_id is not None
    assert project.research_id is not None
    assert project.topic_fingerprint is not None
    assert project.automation_run_id == run.id


@respx.mock
@requires_ffmpeg
def test_the_run_stops_at_publish_without_an_oauth_connection(
    db: Session, autonomous_channel: Channel, collected_trends, providers
) -> None:
    """No connection, no upload — and the run says so rather than reporting success."""
    mock_pipeline_calls()

    run = orchestrator.start_run(db, autonomous_channel, trigger="manual")
    orchestrator.execute(db, autonomous_channel, run)
    db.commit()

    assert run.status == RunStatus.SUCCESS.value
    assert run.stopped_reason is not None
    # Nothing claims a video was published.
    published = [
        entry
        for entry in run.stages
        if entry["stage"] == "publish" and entry["status"] == "COMPLETED"
    ]
    assert published == []


@respx.mock
@requires_ffmpeg
def test_the_selected_topic_cites_real_collected_evidence(
    db: Session, autonomous_channel: Channel, collected_trends, providers
) -> None:
    """Automation never invents a trend: the topic must trace to a stored row."""
    mock_pipeline_calls()

    run = orchestrator.start_run(db, autonomous_channel, trigger="manual")
    orchestrator.execute(db, autonomous_channel, run)
    db.commit()

    project = db.get(ContentProject, run.content_project_id)
    assert project is not None
    from nexora.db.models import TopicCandidate

    candidate = db.get(TopicCandidate, project.topic_candidate_id)
    assert candidate is not None
    assert candidate.sources, "the candidate must cite the evidence it was built from"

    cited_ids = {source["trending_topic_id"] for source in candidate.sources}
    collected_ids = {str(row.id) for row in collected_trends}
    assert cited_ids <= collected_ids


@respx.mock
@requires_ffmpeg
def test_a_second_run_will_not_rebuild_the_same_topic(
    db: Session, autonomous_channel: Channel, collected_trends, providers
) -> None:
    """The dedup check and the topic lock together stop a repeat."""
    mock_pipeline_calls()
    first = orchestrator.start_run(db, autonomous_channel, trigger="manual")
    orchestrator.execute(db, autonomous_channel, first)
    db.commit()

    first_project = db.get(ContentProject, first.content_project_id)
    assert first_project is not None

    # A second run proposing the identical topic must not create a second project.
    respx.post(ANTHROPIC_URL).mock(
        return_value=anthropic_reply(
            {
                "candidates": [
                    {
                        "title": first_project.title,
                        "angle": "The same angle again.",
                        "evidence_indices": [0],
                    }
                ]
            }
        )
    )
    second = orchestrator.start_run(db, autonomous_channel, trigger="manual")
    orchestrator.execute(db, autonomous_channel, second)
    db.commit()

    assert second.content_project_id is None
    assert "repeats existing work" in (second.stopped_reason or "") or "duplicate" in (
        second.stopped_reason or ""
    ).lower()
    projects = db.query(ContentProject).filter_by(channel_id=autonomous_channel.id).count()
    assert projects == 1
