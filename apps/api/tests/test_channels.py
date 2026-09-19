"""Channel defaults and the automation guardrails."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import AuditLog, AutomationSettings, Channel, ChannelSettings, YouTubeConnection
from nexora.services import channels as channel_service


def test_new_channel_uses_required_safe_defaults(auth_client: TestClient, db: Session) -> None:
    response = auth_client.post("/api/channels", json={})
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["name"] == "NEXORA Global"
    assert body["primary_language"] == "en"
    assert body["secondary_language"] == "bn"
    assert body["timezone"] == "Asia/Dhaka"
    assert body["categories"] == ["business", "technology", "ai", "future"]

    automation = db.execute(select(AutomationSettings)).scalars().one()
    assert automation.autopilot_enabled is False, "Autopilot must default to OFF"
    assert automation.auto_publish_enabled is False, "Auto-publishing must default to OFF"
    assert automation.require_human_approval is True, "Human approval must default to REQUIRED"
    assert automation.max_videos_per_day == 1
    assert automation.mode == "assisted"
    assert automation.emergency_stop is False

    settings = db.execute(select(ChannelSettings)).scalars().one()
    assert settings.default_video_format == "long_form"
    assert settings.target_duration_min_seconds == 300
    assert settings.target_duration_max_seconds == 600

    connection = db.execute(select(YouTubeConnection)).scalars().one()
    assert connection.status == "not_connected"


def test_channel_creation_is_audited(auth_client: TestClient, db: Session) -> None:
    auth_client.post("/api/channels", json={})
    entry = db.execute(select(AuditLog).where(AuditLog.action == "channel.created")).scalars().one()
    assert entry.entity_type == "channel"


def test_invalid_timezone_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/api/channels", json={"timezone": "Mars/Olympus"})
    assert response.status_code == 422
    assert "timezone" in response.json()["message"].lower()


def test_invalid_category_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/api/channels", json={"categories": ["crypto-pump"]})
    assert response.status_code == 422


def test_duplicate_channel_name_conflicts(auth_client: TestClient) -> None:
    assert auth_client.post("/api/channels", json={"name": "Dup"}).status_code == 201
    assert auth_client.post("/api/channels", json={"name": "Dup"}).status_code == 409


def test_channel_of_another_user_is_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.services import auth as auth_service

    other = auth_service.register_user(
        db, email="other@nexora.test", password="another-long-password", display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Other Channel")
    db.commit()

    response = auth_client.get(f"/api/channels/{other_channel.id}")
    assert response.status_code == 403


def test_auto_publish_cannot_be_enabled_while_approval_required(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.patch(
        f"/api/channels/{channel.id}/automation", json={"auto_publish_enabled": True}
    )
    assert response.status_code == 422
    assert "human approval" in response.json()["message"]


def test_auto_publish_allowed_only_when_approval_explicitly_disabled(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.patch(
        f"/api/channels/{channel.id}/automation",
        json={"auto_publish_enabled": True, "require_human_approval": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["auto_publish_enabled"] is True


def test_autonomous_mode_requires_autopilot_on(auth_client: TestClient, channel: Channel) -> None:
    rejected = auth_client.patch(f"/api/channels/{channel.id}/automation", json={"mode": "autonomous"})
    assert rejected.status_code == 422

    accepted = auth_client.patch(
        f"/api/channels/{channel.id}/automation",
        json={"mode": "autonomous", "autopilot_enabled": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["mode"] == "autonomous"


def test_emergency_stop_disables_autopilot_and_blocks_re_enable(
    auth_client: TestClient, channel: Channel, db: Session
) -> None:
    auth_client.patch(f"/api/channels/{channel.id}/automation", json={"autopilot_enabled": True})

    stop = auth_client.post(
        f"/api/channels/{channel.id}/automation/emergency-stop", json={"reason": "bad render"}
    )
    assert stop.status_code == 200
    body = stop.json()
    assert body["emergency_stop"] is True
    assert body["autopilot_enabled"] is False
    assert body["auto_publish_enabled"] is False

    blocked = auth_client.patch(
        f"/api/channels/{channel.id}/automation", json={"autopilot_enabled": True}
    )
    assert blocked.status_code == 422
    assert "Emergency stop" in blocked.json()["message"]

    cleared = auth_client.post(f"/api/channels/{channel.id}/automation/clear-emergency-stop")
    assert cleared.status_code == 200
    assert cleared.json()["emergency_stop"] is False
    # Clearing the stop must NOT silently re-enable automation.
    assert cleared.json()["autopilot_enabled"] is False


def test_emergency_stop_cancels_queued_publish_jobs(
    auth_client: TestClient, channel: Channel, db: Session
) -> None:
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import YOUTUBE_UPLOAD

    job = job_queue.enqueue(db, YOUTUBE_UPLOAD, channel_id=channel.id, payload={})
    db.commit()

    response = auth_client.post(
        f"/api/channels/{channel.id}/automation/emergency-stop", json={"reason": "stop now"}
    )
    assert response.json()["cancelled_jobs"] == 1
    db.refresh(job)
    assert job.status == "CANCELLED"


def test_settings_duration_bounds_validated(auth_client: TestClient, channel: Channel) -> None:
    response = auth_client.patch(
        f"/api/channels/{channel.id}/settings",
        json={"target_duration_min_seconds": 900, "target_duration_max_seconds": 300},
    )
    assert response.status_code == 422
