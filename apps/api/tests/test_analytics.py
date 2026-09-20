"""Channel-scoped analytics: measured, never modelled, never cross-channel.

Every claim this module can produce must carry an observation period, a sample size
and the baseline it was compared against. These tests hold that, and hold the line
against the two failure modes that matter: inventing a number, and implying a cause.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable
from nexora.db.models import (
    AnalyticsSnapshot,
    Channel,
    ContentPerformanceFeature,
    User,
    YouTubeConnection,
    YouTubeVideo,
)
from nexora.services import analytics as analytics_service
from nexora.services import channels as channel_service

ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
NOW = datetime.now(UTC)

#: A TEST FIXTURE Analytics API response. The API returns a column table, not an object.
CORE_REPORT = {
    "kind": "youtubeAnalytics#resultTable",
    "columnHeaders": [
        {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
        {"name": "likes", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "dislikes", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "comments", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "shares", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "subscribersGained", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "subscribersLost", "columnType": "METRIC", "dataType": "INTEGER"},
    ],
    "rows": [[4210, 9800, 140, 38.5, 260, 3, 47, 18, 62, 4]],
}

IMPRESSION_REPORT = {
    "kind": "youtubeAnalytics#resultTable",
    "columnHeaders": [
        {"name": "impressions", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "impressionClickThroughRate", "columnType": "METRIC", "dataType": "FLOAT"},
    ],
    "rows": [[81000, 5.2]],
}


@pytest.fixture
def connected(db: Session, channel: Channel, monkeypatch):
    """A channel whose owner granted the analytics scope."""
    monkeypatch.setattr(settings, "youtube_client_id", "fixture-client-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "fixture-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())

    from nexora.core.crypto import encrypt_str

    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = "connected"
    connection.youtube_channel_id = "UCFIXTURECHANNELID000000"
    connection.access_token_encrypted = encrypt_str("fixture-access-token")
    connection.refresh_token_encrypted = encrypt_str("fixture-refresh-token")
    connection.token_expires_at = NOW + timedelta(hours=1)
    connection.has_analytics_scope = True
    connection.scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]
    db.commit()
    return connection


def make_video(
    db: Session,
    channel: Channel,
    *,
    index: int,
    views: int | None,
    published_days_ago: int = 30,
    category: str | None = None,
    title: str | None = None,
) -> YouTubeVideo:
    video = YouTubeVideo(
        channel_id=channel.id,
        youtube_video_id=f"fixtureVideo{index}",
        title=title or f"TEST FIXTURE video {index}",
        privacy_status="public",
        published_at=NOW - timedelta(days=published_days_ago),
    )
    db.add(video)
    db.flush()
    db.add(
        ContentPerformanceFeature(
            channel_id=channel.id,
            video_id=video.id,
            topic_category=category,
            views=views,
            published_weekday=(NOW - timedelta(days=published_days_ago)).weekday(),
            duration_seconds=480,
            title_has_number=True,
            title_has_question=False,
            created_at=NOW,
        )
    )
    db.flush()
    return video


# ------------------------------------------------------------------- collection
@respx.mock
def test_a_snapshot_stores_only_what_youtube_returned(
    db: Session, channel: Channel, connected
) -> None:
    respx.get(ANALYTICS_URL).mock(
        side_effect=[
            httpx.Response(200, json=CORE_REPORT),
            httpx.Response(200, json=IMPRESSION_REPORT),
        ]
    )

    snapshot = analytics_service.collect_channel_snapshot(db, channel, period_days=28)
    db.commit()

    assert snapshot.metrics["views"] == 4210
    assert snapshot.metrics["watch_time_minutes"] == 9800
    assert snapshot.metrics["click_through_rate"] == 5.2
    # Revenue needs a further consent that was not granted, so it is absent, not zero.
    assert "estimated_revenue" not in snapshot.metrics
    assert "estimated_revenue" in snapshot.unavailable_metrics
    assert snapshot.is_complete is False


@respx.mock
def test_a_failed_impressions_report_costs_only_impressions(
    db: Session, channel: Channel, connected
) -> None:
    """Losing the core metrics to chase impressions would be a bad trade."""
    respx.get(ANALYTICS_URL).mock(
        side_effect=[
            httpx.Response(200, json=CORE_REPORT),
            httpx.Response(400, json={"error": {"message": "unsupported metric"}}),
        ]
    )

    snapshot = analytics_service.collect_channel_snapshot(db, channel)
    db.commit()

    assert snapshot.metrics["views"] == 4210
    assert "impressions" not in snapshot.metrics
    assert {"impressions", "click_through_rate"} <= set(snapshot.unavailable_metrics)


def test_collection_without_the_analytics_scope_refuses(
    db: Session, channel: Channel, connected
) -> None:
    """It does not quietly fall back to public data that cannot supply these metrics."""
    connected.has_analytics_scope = False
    connected.scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    db.commit()

    with pytest.raises(ProviderUnavailable, match="analytics scope"):
        analytics_service.collect_channel_snapshot(db, channel)

    assert db.query(AnalyticsSnapshot).count() == 0


@respx.mock
def test_a_null_metric_is_unavailable_not_zero(
    db: Session, channel: Channel, connected
) -> None:
    report = {
        "columnHeaders": CORE_REPORT["columnHeaders"],
        "rows": [[4210, None, 140, 38.5, 260, 3, 47, 18, 62, 4]],
    }
    respx.get(ANALYTICS_URL).mock(
        side_effect=[
            httpx.Response(200, json=report),
            httpx.Response(200, json=IMPRESSION_REPORT),
        ]
    )

    snapshot = analytics_service.collect_channel_snapshot(db, channel)
    db.commit()

    assert "watch_time_minutes" not in snapshot.metrics
    assert "watch_time_minutes" in snapshot.unavailable_metrics


@respx.mock
def test_an_empty_report_yields_no_invented_numbers(
    db: Session, channel: Channel, connected
) -> None:
    empty = {"columnHeaders": CORE_REPORT["columnHeaders"], "rows": []}
    respx.get(ANALYTICS_URL).mock(
        side_effect=[httpx.Response(200, json=empty), httpx.Response(200, json=empty)]
    )

    snapshot = analytics_service.collect_channel_snapshot(db, channel)
    db.commit()

    assert snapshot.metrics == {}
    assert snapshot.is_complete is False
    assert len(snapshot.unavailable_metrics) >= 10


# --------------------------------------------------------------------- baselines
def test_a_baseline_needs_a_minimum_sample(db: Session, channel: Channel) -> None:
    for index in range(analytics_service.MIN_SAMPLE_SIZE - 1):
        make_video(db, channel, index=index, views=1000 + index)
    db.commit()

    result = analytics_service.channel_baseline(db, channel, "views")

    assert result.baseline is None
    assert result.status == "INSUFFICIENT_DATA"
    assert result.sample_size == analytics_service.MIN_SAMPLE_SIZE - 1
    assert f"At least {analytics_service.MIN_SAMPLE_SIZE}" in result.reason


def test_a_baseline_states_its_sample_size_and_period(db: Session, channel: Channel) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=(index + 1) * 100)
    db.commit()

    result = analytics_service.channel_baseline(db, channel, "views")
    payload = result.to_dict()

    assert result.baseline is not None
    assert payload["sample_size"] == 6
    assert payload["observation_period"]["days"] > 0
    assert payload["median"] == 350.0
    assert "This channel only" in payload["basis"]


def test_a_video_without_recorded_views_is_left_out_of_the_median(
    db: Session, channel: Channel
) -> None:
    """A missing metric must never be counted as zero — it would drag the median down."""
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    make_video(db, channel, index=99, views=None)
    db.commit()

    result = analytics_service.channel_baseline(db, channel, "views")
    assert result.baseline.sample_size == 6
    assert result.baseline.median == 1000.0


def test_a_video_published_too_recently_is_left_out(db: Session, channel: Channel) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    make_video(db, channel, index=99, views=1, published_days_ago=1)
    db.commit()

    assert analytics_service.channel_baseline(db, channel, "views").baseline.sample_size == 6


def test_a_baseline_never_crosses_a_channel_boundary(db: Session, user: User) -> None:
    """A median across two audiences would describe neither."""
    kids = channel_service.create_channel(db, user, name="Kids", categories=["kids"])
    tech = channel_service.create_channel(db, user, name="Tech", categories=["technology"])
    db.flush()

    for index in range(6):
        make_video(db, kids, index=index, views=100)
    for index in range(6):
        make_video(db, tech, index=index + 50, views=100000)
    db.commit()

    assert analytics_service.channel_baseline(db, kids, "views").baseline.median == 100.0
    assert analytics_service.channel_baseline(db, tech, "views").baseline.median == 100000.0


def test_an_unsupported_metric_is_refused_rather_than_guessed(
    db: Session, channel: Channel
) -> None:
    result = analytics_service.channel_baseline(db, channel, "virality")
    assert result.baseline is None
    assert "not a metric NEXORA compares" in result.reason


# ------------------------------------------------------------------- comparison
def test_a_comparison_carries_period_sample_size_and_baseline(
    db: Session, channel: Channel
) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=3000)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    payload = result.to_dict()

    assert isinstance(result, analytics_service.Comparison)
    assert payload["compared_against"]["sample_size"] == 6
    assert payload["compared_against"]["median"] == 1000.0
    assert payload["compared_against"]["observation_period"]["days"] > 0
    assert "3,000 views" in payload["observation"]
    assert "median of 1,000" in payload["observation"]


def test_a_video_is_excluded_from_its_own_baseline(db: Session, channel: Channel) -> None:
    """Comparing a video against a median it helped set understates the difference."""
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=50000)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    assert result.baseline.median == 1000.0
    assert result.baseline.sample_size == 6


def test_a_comparison_never_claims_a_cause_or_a_forecast(
    db: Session, channel: Channel
) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=9000)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    text = str(result.to_dict()).lower()

    for phrase in (
        "will get", "will go viral", "caused the", "this caused", "guaranteed",
        "you will earn", "predicted", "expect to", "because it was",
    ):
        assert phrase not in text, phrase

    assert "not reasons it performed" in result.to_dict()["not_a_cause"]
    assert "possible" in str(result.to_dict()).lower()


def test_factors_are_attributes_not_explanations(db: Session, channel: Channel) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=9000)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    attributes = {factor["attribute"] for factor in result.possible_factors}
    assert {"published_weekday", "duration_seconds"} <= attributes
    for factor in result.possible_factors:
        assert "because" not in factor["note"].lower()


def test_a_comparison_without_a_baseline_says_why(db: Session, channel: Channel) -> None:
    subject = make_video(db, channel, index=1, views=5000)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    assert isinstance(result, analytics_service.BaselineResult)
    assert result.status == "INSUFFICIENT_DATA"


def test_a_video_with_no_recorded_metric_says_so(db: Session, channel: Channel) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=None)
    db.commit()

    result = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    assert isinstance(result, analytics_service.BaselineResult)
    assert "Collect its analytics first" in result.reason


def test_an_observation_records_the_three_required_numbers(
    db: Session, channel: Channel
) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=1000)
    subject = make_video(db, channel, index=99, views=4000)
    db.commit()

    comparison = analytics_service.compare_video_to_baseline(db, channel, subject, "views")
    row = analytics_service.record_observation(db, channel, subject, comparison)
    db.commit()

    assert row.measured["sample_size"] == 6
    assert row.measured["baseline_median"] == 1000.0
    assert row.measured["observation_period_days"] > 0
    assert "cannot explain it" in row.suggested_investigation


# ------------------------------------------------------------------- categories
def test_category_performance_reports_per_category_sample_sizes(
    db: Session, channel: Channel
) -> None:
    for index in range(6):
        make_video(db, channel, index=index, views=2000, category="ai")
    for index in range(2):
        make_video(db, channel, index=index + 50, views=500, category="gaming")
    db.commit()

    body = analytics_service.category_performance(db, channel)
    by_key = {item["category"]: item for item in body["categories"]}

    assert by_key["ai"]["status"] == "AVAILABLE"
    assert by_key["ai"]["median_views"] == 2000.0
    assert by_key["ai"]["sample_size"] == 6

    # Two videos is not a category norm, and it says so rather than reporting a median.
    assert by_key["gaming"]["status"] == "INSUFFICIENT_DATA"
    assert by_key["gaming"]["median_views"] is None
    assert by_key["gaming"]["sample_size"] == 2

    assert body["observation_period"]["days"] == 180
    assert "do not predict" in body["note"]


# -------------------------------------------------------------------------- API
def test_every_analytics_endpoint_requires_authentication(client: TestClient) -> None:
    import uuid

    video_id = uuid.uuid4()
    for method, path in [
        ("get", "/api/analytics/overview"),
        ("post", "/api/analytics/collect"),
        ("get", "/api/analytics/baseline"),
        ("get", f"/api/analytics/video/{video_id}"),
        ("post", f"/api/analytics/video/{video_id}/observe"),
        ("get", "/api/analytics/observations"),
        ("get", "/api/analytics/categories"),
        ("get", "/api/analytics/snapshots"),
        ("get", "/api/analytics/videos"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_another_users_analytics_are_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Theirs")
    db.commit()

    assert (
        auth_client.get(f"/api/analytics/overview?channel_id={other_channel.id}").status_code
        == 403
    )


def test_the_overview_reports_nothing_collected_rather_than_zeros(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/analytics/overview").json()

    assert body["snapshot"] is None
    assert body["baselines"]["views"]["status"] == "INSUFFICIENT_DATA"
    assert body["baselines"]["views"]["median"] is None
    assert "never compares one channel against another" in body["scope_note"]
    assert "does not predict views" in body["no_forecast_note"]


def test_the_overview_reports_the_oauth_app_as_not_configured(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/analytics/overview").json()
    assert body["oauth_app"]["status"] == "NOT CONFIGURED"


def test_collecting_without_a_connection_is_an_error_not_an_empty_snapshot(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.post("/api/analytics/collect")
    assert response.status_code in {502, 503}


def test_analytics_writes_require_csrf(auth_client: TestClient, channel: Channel) -> None:
    from nexora.services import auth as auth_service

    auth_client.headers.pop(auth_service.CSRF_HEADER)
    assert auth_client.post("/api/analytics/collect").status_code == 403


def test_the_video_list_shows_null_for_uncollected_metrics(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    video = YouTubeVideo(
        channel_id=channel.id,
        youtube_video_id="fixtureNoMetrics",
        title="TEST FIXTURE never collected",
        published_at=NOW - timedelta(days=10),
    )
    db.add(video)
    db.commit()

    body = auth_client.get("/api/analytics/videos").json()
    item = body["items"][0]

    assert item["has_metrics"] is False
    assert item["metrics"]["views"] is None
    assert item["metrics"]["click_through_rate"] is None


def test_the_baseline_endpoint_states_the_minimum_sample_size(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/analytics/baseline").json()
    assert body["minimum_sample_size"] == analytics_service.MIN_SAMPLE_SIZE
    assert body["status"] == "INSUFFICIENT_DATA"


def test_observations_are_labelled_as_measurements(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/analytics/observations").json()
    assert "never causes" in body["note"]
    assert "do not forecast" in body["note"]
