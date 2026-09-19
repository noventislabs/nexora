"""The dashboard never reports 0 for data it does not have."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.db.models import AnalyticsSnapshot, Channel, YouTubeConnection
from nexora.db.models.enums import AnalyticsScope, ConnectionStatus


def test_overview_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/dashboard/overview").status_code == 401


def test_overview_without_a_channel_is_a_clear_404(auth_client: TestClient) -> None:
    response = auth_client.get("/api/dashboard/overview")
    assert response.status_code == 404
    assert "No channel" in response.json()["message"]


def test_autopilot_reported_off_by_default(auth_client: TestClient, channel: Channel) -> None:
    body = auth_client.get("/api/dashboard/overview").json()
    assert body["autopilot"]["enabled"] is False
    assert body["autopilot"]["auto_publish_enabled"] is False
    assert body["autopilot"]["require_human_approval"] is True
    assert body["autopilot"]["mode"] == "assisted"


def test_today_counters_are_real_counts(auth_client: TestClient, channel: Channel) -> None:
    """These are genuine zeros: the system counted its own work and found none."""
    today = auth_client.get("/api/dashboard/overview").json()["today"]
    for key in ("trending_topics", "ideas_generated", "scripts_ready", "videos_rendering"):
        assert today[key] == {"value": 0, "available": True, "unavailable_reason": None}
    assert today["timezone"] == "Asia/Dhaka"


def test_channel_metrics_are_unavailable_not_zero_when_youtube_is_not_connected(
    auth_client: TestClient, channel: Channel
) -> None:
    summary = auth_client.get("/api/dashboard/overview").json()["channel_summary"]

    for key in ("subscribers", "views", "videos"):
        assert summary[key]["value"] is None, f"{key} must not be fabricated"
        assert summary[key]["available"] is False
        assert summary[key]["unavailable_reason"]

    assert summary["revenue"]["value"] is None
    assert "monetization" in summary["revenue"]["unavailable_reason"].lower()
    assert summary["youtube"]["status"] == "NOT CONNECTED"
    assert summary["last_updated"] is None


def test_connected_channel_without_snapshot_still_reports_unavailable(
    auth_client: TestClient, channel: Channel, db: Session
) -> None:
    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = ConnectionStatus.CONNECTED.value
    connection.youtube_channel_id = "UC_test"
    connection.youtube_channel_title = "NEXORA Global"
    db.commit()

    summary = auth_client.get("/api/dashboard/overview").json()["channel_summary"]
    assert summary["subscribers"]["value"] is None
    assert "analytics snapshot" in summary["subscribers"]["unavailable_reason"]


def test_metrics_absent_from_a_snapshot_stay_unavailable(
    auth_client: TestClient, channel: Channel, db: Session
) -> None:
    """A snapshot that omits a metric must not be rendered as zero."""
    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = ConnectionStatus.CONNECTED.value
    connection.youtube_channel_id = "UC_test"
    connection.has_monetary_scope = False
    db.add(
        AnalyticsSnapshot(
            channel_id=channel.id,
            scope=AnalyticsScope.CHANNEL.value,
            source="youtube_data_api",
            captured_at=datetime.now(UTC),
            metrics={"view_count": 1234, "video_count": 7},  # subscriber_count deliberately absent
            unavailable_metrics=["subscriber_count"],
        )
    )
    db.commit()

    summary = auth_client.get("/api/dashboard/overview").json()["channel_summary"]
    assert summary["views"] == {"value": 1234, "available": True, "unavailable_reason": None}
    assert summary["videos"]["value"] == 7
    assert summary["subscribers"]["value"] is None
    assert summary["subscribers"]["available"] is False
    assert summary["revenue"]["value"] is None
    assert summary["data_source"] == "youtube_data_api"
    assert summary["data_age_seconds"] is not None
    assert summary["last_updated"] is not None


def test_pipeline_and_queue_sections_present(auth_client: TestClient, channel: Channel) -> None:
    body = auth_client.get("/api/dashboard/overview").json()
    assert body["pipeline"]["draft"] == 0
    assert body["queue"]["QUEUED"] == 0
    assert body["pending_approvals"] == 0
