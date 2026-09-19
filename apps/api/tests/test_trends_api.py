"""Trend and topic API: authorization, filtering, scan jobs and NOT_CONFIGURED paths."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.db.models import AuditLog, Channel, Job, TopicCandidate, TrendingTopic
from nexora.services import auth as auth_service
from nexora.services import channels as channel_service
from nexora.services.trends import sources as source_service
from nexora.services.trends.scan import scan_channel
from tests.conftest import TEST_PASSWORD
from tests.fixtures import feeds

FEED = "https://fixture-api.invalid/feed.xml"


def add_feed(db: Session, channel: Channel, name: str = "Fixture Feed") -> None:
    source_service.create_source(
        db,
        channel_id=channel.id,
        kind="rss",
        name=name,
        config={"url": FEED, "category": "technology"},
    )
    db.commit()


# ------------------------------------------------------------------- authorization
def test_every_trend_endpoint_requires_authentication(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/trends"),
        ("get", "/api/trends/sources"),
        ("post", "/api/trends/sources"),
        ("post", "/api/trends/scan"),
        ("get", "/api/trends/scoring-model"),
        ("get", "/api/topics"),
        ("post", "/api/topics/generate"),
    ]:
        response = (
            client.post(path, json={}) if method == "post" else client.get(path)
        )
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_another_users_channel_is_not_readable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Other")
    db.commit()
    assert auth_client.get(f"/api/trends?channel_id={other_channel.id}").status_code == 403


def test_state_changing_trend_calls_require_csrf(auth_client: TestClient, channel: Channel) -> None:
    auth_client.headers.pop(auth_service.CSRF_HEADER)
    response = auth_client.post(
        "/api/trends/sources", json={"kind": "rss", "name": "X", "config": {"url": FEED}}
    )
    assert response.status_code == 403


# ------------------------------------------------------------------------- sources
def test_sources_endpoint_reports_provider_level_status(
    auth_client: TestClient, channel: Channel, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", "")
    monkeypatch.setattr(settings, "reddit_client_id", "")

    body = auth_client.get("/api/trends/sources").json()
    assert body["providers"]["rss"]["status"] == "AVAILABLE"
    assert body["providers"]["youtube_data_api"]["status"] == "NOT CONFIGURED"
    assert body["providers"]["youtube_data_api"]["missing_settings"] == ["YOUTUBE_API_KEY"]
    assert body["providers"]["reddit"]["status"] == "NOT CONFIGURED"
    assert body["supported_kinds"] == ["youtube_data_api", "rss", "reddit"]


def test_create_source_rejects_a_bad_feed_url(auth_client: TestClient, channel: Channel) -> None:
    response = auth_client.post(
        "/api/trends/sources",
        json={"kind": "rss", "name": "Bad", "config": {"url": "file:///etc/passwd"}},
    )
    assert response.status_code == 422


def test_create_and_update_a_source_is_audited(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    created = auth_client.post(
        "/api/trends/sources",
        json={
            "kind": "rss",
            "name": "Fixture Feed",
            "config": {"url": FEED, "category": "technology"},
            "reliability": 0.8,
            "region": "GLOBAL",
            "min_interval_minutes": 120,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["reliability"] == 0.8
    assert body["min_interval_minutes"] == 120
    assert body["availability"]["status"] == "AVAILABLE"
    assert body["due_now"] is True

    updated = auth_client.patch(
        f"/api/trends/sources/{body['id']}", json={"enabled": False, "reliability": 0.5}
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["availability"]["status"] == "NOT CONNECTED"

    actions = {entry.action for entry in db.query(AuditLog).all()}
    assert {"trends.source_created", "trends.source_updated"} <= actions


def test_seed_defaults_endpoint(auth_client: TestClient, channel: Channel) -> None:
    response = auth_client.post("/api/trends/sources/seed-defaults")
    assert response.status_code == 201
    assert response.json()["created"] == len(source_service.DEFAULT_RSS_SOURCES)
    assert all(item["kind"] == "rss" for item in response.json()["items"])


# ---------------------------------------------------------------------------- scan
def test_scan_without_sources_is_a_clear_404(auth_client: TestClient, channel: Channel) -> None:
    response = auth_client.post("/api/trends/scan", json={"background": False})
    assert response.status_code == 404
    assert "no trend sources" in response.json()["message"]


@respx.mock
def test_inline_scan_returns_per_source_results(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    add_feed(db, channel)

    response = auth_client.post("/api/trends/scan", json={"background": False})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "inline"
    assert body["stored"] == 2
    assert body["sources"][0]["status"] == "SUCCESS"
    assert body["duration_seconds"] >= 0


def test_background_scan_enqueues_a_job(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    add_feed(db, channel)
    response = auth_client.post("/api/trends/scan", json={"background": True})
    assert response.status_code == 200
    assert response.json()["mode"] == "queued"

    job = db.query(Job).filter(Job.type == "trend_scan").one()
    assert job.status == "QUEUED"
    assert job.channel_id == channel.id


@respx.mock
def test_scan_job_runs_through_the_worker(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    add_feed(db, channel)
    auth_client.post("/api/trends/scan", json={"background": True})
    db.commit()

    from nexora.queue.worker import Worker

    assert Worker().run_once() is True
    db.expire_all()

    job = db.query(Job).filter(Job.type == "trend_scan").one()
    assert job.status == "SUCCESS"
    assert job.result["stored"] == 2
    assert db.query(TrendingTopic).count() == 2


# -------------------------------------------------------------------------- listing
@respx.mock
def test_list_trends_filters_and_hides_duplicates(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    add_feed(db, channel)
    scan_channel(db, channel)
    db.commit()

    body = auth_client.get("/api/trends").json()
    assert body["total"] == 2
    item = body["items"][0]
    assert item["source"]["kind"] == "rss"
    assert item["discovered_at"]
    assert item["freshness"]["state"] in {"FRESH", "STALE"}
    assert item["engagement"] == {}
    assert "opportunity_score" in item

    assert auth_client.get("/api/trends?category=technology").json()["total"] == 2
    assert auth_client.get("/api/trends?category=cooking").json()["total"] == 0
    assert auth_client.get("/api/trends?source_kind=reddit").json()["total"] == 0
    assert auth_client.get("/api/trends?min_score=101").status_code == 422


@respx.mock
def test_trend_detail_lists_corroborating_items(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, text=feeds.RSS_2_0))
    other = "https://fixture-api-b.invalid/feed.xml"
    respx.get(other).mock(
        return_value=httpx.Response(200, text=feeds.RSS_2_0.replace("fixture-item-", "b-item-"))
    )
    add_feed(db, channel, "Feed A")
    source_service.create_source(
        db, channel_id=channel.id, kind="rss", name="Feed B", config={"url": other}
    )
    db.commit()
    scan_channel(db, channel)
    db.commit()

    listing = auth_client.get("/api/trends").json()
    detail = auth_client.get(f"/api/trends/{listing['items'][0]['id']}").json()
    assert detail["corroboration_count"] == 2
    assert len(detail["corroborating_items"]) == 1
    assert detail["corroborating_items"][0]["source_name"] == "Feed B"


def test_scoring_model_is_documented_and_makes_no_prediction_claim(
    auth_client: TestClient,
) -> None:
    body = auth_client.get("/api/trends/scoring-model").json()
    assert body["name"] == "Opportunity Score"
    assert round(sum(body["weights"].values()), 6) == 1.0
    assert set(body["components"]) == set(body["weights"])
    text = str(body).lower()
    assert "not a probability" in text
    for forbidden in ("guaranteed", "will go viral", "predicted revenue"):
        assert forbidden not in text


# -------------------------------------------------------------------------- topics
def test_topic_generation_without_an_llm_is_not_configured(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    """The critical honesty case: no LLM means no topics, not invented ones."""
    from datetime import UTC, datetime

    from nexora.db.models import TrendSource

    add_feed(db, channel)
    source = db.query(TrendSource).one()
    db.add(
        TrendingTopic(
            source_id=source.id,
            channel_id=channel.id,
            source_kind="rss",
            source_name="Fixture Feed",
            external_id="e1",
            dedupe_hash="a" * 64,
            content_hash="b" * 64,
            title="Fixture story about AI chips",
            discovered_at=datetime.now(UTC),
        )
    )
    db.commit()

    response = auth_client.post("/api/topics/generate", json={"count": 3, "background": False})
    assert response.status_code == 503
    assert response.json()["code"] == "provider_not_configured"
    assert "LLM_PROVIDER" in str(response.json())
    assert db.query(TopicCandidate).count() == 0, "nothing may be invented on the unconfigured path"


def test_topic_generation_without_evidence_says_so(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.post("/api/topics/generate", json={"count": 3, "background": False})
    assert response.status_code == 404
    assert "trend scan" in response.json()["message"]


def test_list_topics_is_empty_before_any_generation(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/topics").json()
    assert body == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
        "channel_id": str(channel.id),
    }
