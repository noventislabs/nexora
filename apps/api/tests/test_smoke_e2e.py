"""End-to-end first-run workflow against the real API, database and queue.

This exercises the Phase 1 slice of the product's first-user experience:
register → create workspace/channel → inspect capabilities → configure limits →
observe the dashboard and the audit trail.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.services.auth import CSRF_HEADER

PASSWORD = "first-run-operator-password"


def test_first_run_workflow(client: TestClient, db: Session) -> None:
    # 1. The deployment is unclaimed.
    assert client.get("/api/auth/registration-open").json()["open"] is True

    # 2. Create the operator account; the session and CSRF token come back together.
    registered = client.post(
        "/api/auth/register",
        json={"email": "owner@nexora.test", "password": PASSWORD, "display_name": "Owner"},
    )
    assert registered.status_code == 201, registered.text
    client.headers[CSRF_HEADER] = registered.json()["csrf_token"]

    # 3. Before a channel exists the dashboard says so instead of inventing one.
    assert client.get("/api/dashboard/overview").status_code == 404

    # 4. Create the workspace channel with the product's defaults.
    created = client.post("/api/channels", json={})
    assert created.status_code == 201
    channel_id = created.json()["id"]

    # 5. Capabilities tell the operator exactly what this deployment can do.
    capabilities = client.get("/api/system/capabilities").json()
    assert capabilities["youtube_oauth"]["status"] == "NOT CONFIGURED"
    assert capabilities["llm"]["status"] == "NOT CONFIGURED"
    assert capabilities["storage"]["status"] in {"HEALTHY", "AVAILABLE"}

    # 6. The dashboard reports safe defaults and no fabricated channel metrics.
    overview = client.get("/api/dashboard/overview").json()
    assert overview["autopilot"] == {
        "enabled": False,
        "mode": "assisted",
        "auto_publish_enabled": False,
        "require_human_approval": True,
        "emergency_stop": False,
        "emergency_stop_reason": None,
        "max_videos_per_day": 1,
    }
    assert overview["channel_summary"]["subscribers"]["value"] is None
    assert overview["channel_summary"]["revenue"]["value"] is None
    assert overview["today"]["trending_topics"]["value"] == 0

    # 7. Configure publishing limits.
    limits = client.patch(
        f"/api/channels/{channel_id}/automation",
        json={"max_videos_per_day": 2, "preferred_publish_hour": 19, "timezone": "Asia/Dhaka"},
    )
    assert limits.status_code == 200
    assert limits.json()["max_videos_per_day"] == 2
    # Turning a limit up must not turn automation on.
    assert limits.json()["autopilot_enabled"] is False

    # 8. Configure editorial defaults.
    settings = client.patch(
        f"/api/channels/{channel_id}/settings",
        json={"target_duration_min_seconds": 360, "target_duration_max_seconds": 600},
    )
    assert settings.status_code == 200

    # 9. Every step above is in the audit trail.
    actions = {entry["action"] for entry in client.get("/api/logs/audit").json()["items"]}
    assert {
        "user.registered",
        "channel.created",
        "automation.settings_updated",
        "channel.settings_updated",
    } <= actions

    # 10. Health reflects the real stack.
    health = client.get("/api/system/health").json()
    components = {component["name"]: component["status"] for component in health["components"]}
    assert components["database"] == "HEALTHY"
    assert components["queue"] == "HEALTHY"

    # 11. Signing out invalidates the session immediately.
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/dashboard/overview").status_code == 401


def test_jobs_endpoint_reports_real_queue_state(client: TestClient, db: Session) -> None:
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import TREND_SCAN
    from nexora.services import auth as auth_service
    from nexora.services import channels as channel_service

    user = auth_service.register_user(
        db, email="ops@nexora.test", password=PASSWORD, display_name="Ops"
    )
    channel = channel_service.create_channel(db, user)
    job_queue.enqueue(db, TREND_SCAN, channel_id=channel.id, payload={"limit": 5})
    db.commit()

    login = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    client.headers[CSRF_HEADER] = login.json()["csrf_token"]

    listing = client.get("/api/jobs").json()
    assert listing["total"] == 1
    assert listing["items"][0]["type"] == "trend_scan"
    assert listing["items"][0]["status"] == "QUEUED"

    summary = client.get("/api/jobs/summary").json()
    assert summary["counts"]["QUEUED"] == 1
    assert summary["counts"]["RUNNING"] == 0
