"""Research, project, script and fact-check API: authorization and NOT_CONFIGURED."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.db.models import Channel, Job, TopicCandidate, TopicResearch
from nexora.services import auth as auth_service
from nexora.services import channels as channel_service
from nexora.services.trends import sources as source_service
from tests.conftest import TEST_PASSWORD
from tests.fixtures import feeds

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "fixture-key")


def anthropic_reply(payload: dict) -> httpx.Response:
    text = json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "id": "msg_fixture",
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": text[1:]}],
            "usage": {},
        },
    )


@pytest.fixture
def candidate(db: Session, channel: Channel) -> TopicCandidate:
    from nexora.db.models import TrendingTopic

    source = source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name="Fixture Feed",
        config={"url": "https://fixture-news.invalid/feed.xml"},
    )
    # Two sources, so a recorded conflict has two resolvable positions to stand on.
    trends = []
    for index, summary in enumerate(
        [
            "Packaging capacity remains the binding constraint on output.",
            "A second association places the addition nearer 25,000 wafer starts.",
        ]
    ):
        trend = TrendingTopic(
            source_id=source.id,
            channel_id=channel.id,
            source_kind="rss",
            source_name="Fixture Feed",
            external_id=f"x{index}",
            dedupe_hash=f"{index:064d}",
            content_hash=f"{index + 900:064d}",
            title=f"TEST FIXTURE: capacity report {index}",
            url=f"https://fixture-news.invalid/articles/{index}",
            summary=summary,
            discovered_at=datetime.now(UTC),
        )
        db.add(trend)
        trends.append(trend)
    db.flush()
    row = TopicCandidate(
        channel_id=channel.id,
        title="Why capacity is the constraint",
        angle="Explains the constraint.",
        status="approved",
        sources=[
            {
                "trending_topic_id": str(trend.id),
                "title": trend.title,
                "url": trend.url,
                "source_name": "Fixture Feed",
                "source_kind": "rss",
                "published_at": None,
            }
            for trend in trends
        ],
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------------------- authorization
def test_every_content_endpoint_requires_authentication(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/research"),
        ("post", "/api/research"),
        ("get", "/api/content"),
        ("post", "/api/content"),
        ("post", "/api/scripts/generate"),
        ("post", "/api/fact-check"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_another_users_channel_is_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Other")
    db.commit()
    assert auth_client.get(f"/api/content?channel_id={other_channel.id}").status_code == 403
    assert auth_client.get(f"/api/research?channel_id={other_channel.id}").status_code == 403


def test_state_changing_calls_require_csrf(auth_client: TestClient, channel: Channel) -> None:
    auth_client.headers.pop(auth_service.CSRF_HEADER)
    response = auth_client.post("/api/research", json={"topic_candidate_id": str(channel.id)})
    assert response.status_code == 403


# ---------------------------------------------------------------- not configured
def test_research_without_an_llm_returns_not_configured(
    auth_client: TestClient, db: Session, channel: Channel, candidate: TopicCandidate
) -> None:
    response = auth_client.post(
        "/api/research", json={"topic_candidate_id": str(candidate.id), "background": False}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "provider_not_configured"
    assert db.query(TopicResearch).count() == 0


def test_script_generation_without_an_llm_returns_not_configured(
    auth_client: TestClient, db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    # Build a project with research, then drop the provider before scripting.
    with respx.mock:
        respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
        auth_client.post(
            "/api/research", json={"topic_candidate_id": str(candidate.id), "background": False}
        )
    created = auth_client.post("/api/content", json={"topic_candidate_id": str(candidate.id)})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    settings.llm_provider = ""
    try:
        response = auth_client.post(
            "/api/scripts/generate", json={"content_project_id": project_id, "background": False}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "provider_not_configured"
    finally:
        settings.llm_provider = "anthropic"


# ----------------------------------------------------------------- full pipeline
@respx.mock
def test_research_to_fact_check_through_the_api(
    auth_client: TestClient, db: Session, channel: Channel, candidate: TopicCandidate, llm
) -> None:
    # 1. Research
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.RESEARCH_RESPONSE))
    research = auth_client.post(
        "/api/research", json={"topic_candidate_id": str(candidate.id), "background": False}
    )
    assert research.status_code == 201, research.text
    body = research.json()
    assert body["status"] == "SUCCESS"
    assert body["document_count"] >= 1
    assert body["documents"][0]["fetch_decision"] == "DISABLED"
    assert body["conflicts"], "recorded conflicts must survive to the API"

    # 2. Project
    created = auth_client.post("/api/content", json={"topic_candidate_id": str(candidate.id)})
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["status"] == "scripting"

    # 3. Script
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.SCRIPT_RESPONSE))
    script = auth_client.post(
        "/api/scripts/generate", json={"content_project_id": project_id, "background": False}
    )
    assert script.status_code == 201, script.text
    assert script.json()["version"]["version"] == 1
    assert script.json()["originality"]["conclusive"] is True

    # 4. Fact check — the fixture contains an unsupported assertion, so this must FAIL.
    respx.post(ANTHROPIC_URL).mock(return_value=anthropic_reply(feeds.FACT_CHECK_RESPONSE))
    check = auth_client.post(
        "/api/fact-check", json={"content_project_id": project_id, "background": False}
    )
    assert check.status_code == 201
    assert check.json()["status"] == "FAIL"
    assert check.json()["blocks_publishing"] is True

    # 5. The project detail shows the whole chain.
    detail = auth_client.get(f"/api/content/{project_id}").json()
    assert detail["research"]["document_count"] >= 1
    assert detail["script"]["current_version"] == 1
    assert detail["fact_check"]["status"] == "FAIL"
    # A failing check must not have advanced the project.
    assert detail["status"] == "scripting"


def test_project_requires_research_first(
    auth_client: TestClient, channel: Channel, candidate: TopicCandidate
) -> None:
    response = auth_client.post("/api/content", json={"topic_candidate_id": str(candidate.id)})
    assert response.status_code == 422
    assert "Research this candidate first" in response.json()["message"]


def test_background_research_enqueues_a_job(
    auth_client: TestClient, db: Session, channel: Channel, candidate: TopicCandidate
) -> None:
    response = auth_client.post(
        "/api/research", json={"topic_candidate_id": str(candidate.id), "background": True}
    )
    assert response.status_code == 201
    assert response.json()["mode"] == "queued"
    job = db.query(Job).filter(Job.type == "research").one()
    assert job.status == "QUEUED"


def test_research_job_without_an_llm_fails_permanently(
    auth_client: TestClient, db: Session, channel: Channel, candidate: TopicCandidate
) -> None:
    """A missing key is a configuration problem; retrying it would be pointless."""
    auth_client.post(
        "/api/research", json={"topic_candidate_id": str(candidate.id), "background": True}
    )
    db.commit()

    from nexora.queue.worker import Worker

    assert Worker().run_once() is True
    db.expire_all()

    job = db.query(Job).filter(Job.type == "research").one()
    assert job.status == "FAILED"
    assert job.permanent_failure is True
    assert "NOT CONFIGURED" in (job.error or "")
