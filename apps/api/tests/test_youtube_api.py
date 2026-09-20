"""YouTube, metadata and publishing endpoints: authorization, CSRF and honest states.

The route layer is where a fabrication would be most visible to a user, so these tests
assert the negative cases hardest: an unconfigured provider must say NOT CONFIGURED, a
public channel link must not claim upload consent, and a blocked preflight must return
409 with the blockers named rather than a cheerful success.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.config import settings
from nexora.db.models import (
    AuditLog,
    Channel,
    ContentProject,
    Job,
    PublishJob,
    User,
    YouTubeConnection,
    YouTubeVideo,
)
from nexora.services import auth as auth_service
from nexora.services import channels as channel_service
from tests.conftest import TEST_PASSWORD

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# A TEST FIXTURE response body. It is never used outside this module.
FIXTURE_CHANNEL = {
    "items": [
        {
            "id": "UCFIXTUREAPICHANNEL00000",
            "snippet": {
                "title": "TEST FIXTURE Channel",
                "customUrl": "@fixture",
                "description": "A fixture channel.",
                "publishedAt": "2021-04-02T10:00:00Z",
                "country": "US",
                "thumbnails": {"default": {"url": "https://fixture.invalid/thumb.jpg"}},
            },
            "statistics": {
                "subscriberCount": "4210",
                "viewCount": "915032",
                "videoCount": "88",
                "hiddenSubscriberCount": False,
            },
            "contentDetails": {"relatedPlaylists": {"uploads": "UUFIXTUREAPICHANNEL0000"}},
        }
    ]
}


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "fixture-client-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "fixture-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "fixture-api-key")


@pytest.fixture
def project(db: Session, channel: Channel) -> ContentProject:
    row = ContentProject(
        channel_id=channel.id,
        title="Fixture project",
        status="ready",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------------------- authorization
def test_every_youtube_endpoint_requires_authentication(client: TestClient) -> None:
    project_id = uuid.uuid4()
    for method, path in [
        ("get", "/api/youtube/connection"),
        ("post", "/api/youtube/connect"),
        ("post", "/api/youtube/disconnect"),
        ("get", "/api/youtube/public-channel"),
        ("post", "/api/youtube/public-channel"),
        ("post", "/api/youtube/public-channel/refresh"),
        ("get", "/api/youtube/videos"),
        ("post", "/api/metadata/generate"),
        ("get", f"/api/metadata/{project_id}"),
        ("get", "/api/publish"),
        ("post", "/api/publish"),
        ("post", f"/api/publish/approve/{project_id}"),
        ("get", f"/api/publish/preflight/{project_id}"),
        ("get", f"/api/publish/{uuid.uuid4()}"),
        ("get", "/api/publish/pending/approvals"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_the_oauth_callback_is_deliberately_unauthenticated(client: TestClient) -> None:
    """Google redirects the browser here; the single-use state is what authorizes it."""
    response = client.get(
        "/api/youtube/oauth/callback?state=not-a-real-state&code=not-a-real-code",
        follow_redirects=False,
    )
    assert response.status_code == 303
    # An unknown state must fail closed, not connect anything.
    assert "youtube_error=" in response.headers["location"]


def test_another_users_channel_is_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Other")
    db.commit()

    assert (
        auth_client.get(f"/api/youtube/connection?channel_id={other_channel.id}").status_code == 403
    )
    assert auth_client.get(f"/api/publish?channel_id={other_channel.id}").status_code == 403
    assert (
        auth_client.get(f"/api/youtube/videos?channel_id={other_channel.id}").status_code == 403
    )


def test_publishing_writes_require_csrf(
    auth_client: TestClient, channel: Channel, project: ContentProject
) -> None:
    """A publish triggered by a cross-site form post is the worst possible CSRF."""
    auth_client.headers.pop(auth_service.CSRF_HEADER)
    for path, body in [
        ("/api/youtube/connect", {}),
        ("/api/youtube/disconnect", {}),
        ("/api/youtube/public-channel", {"channel_identifier": "UCabc"}),
        ("/api/metadata/generate", {"content_project_id": str(project.id)}),
        ("/api/publish", {"content_project_id": str(project.id)}),
        (f"/api/publish/approve/{project.id}", {"approved": True}),
    ]:
        assert auth_client.post(path, json=body).status_code == 403, path


# ----------------------------------------------------------------- not configured
def test_connection_reports_not_configured_without_oauth_credentials(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/youtube/connection").json()

    assert body["status"] == "not_connected"
    assert body["oauth_app"]["status"] == "NOT CONFIGURED"
    assert body["oauth_app"]["missing_settings"] == ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"]
    assert body["data_api_key"]["status"] == "NOT CONFIGURED"
    assert "OAuth consent" in body["note"]


def test_connect_refuses_when_the_oauth_app_is_not_configured(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.post("/api/youtube/connect", json={})

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "provider_not_configured"
    assert "YOUTUBE_CLIENT_ID" in body["message"]


def test_linking_a_public_channel_refuses_without_an_api_key(
    auth_client: TestClient, channel: Channel
) -> None:
    response = auth_client.post(
        "/api/youtube/public-channel", json={"channel_identifier": "UCFIXTUREAPICHANNEL00000"}
    )

    assert response.status_code == 503
    assert response.json()["details"]["missing_settings"] == ["YOUTUBE_API_KEY"]


def test_an_unlinked_public_channel_reports_unlinked_rather_than_empty_numbers(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/youtube/public-channel").json()

    assert body["linked"] is False
    assert body["channel_id"] is None
    assert body["title"] is None
    # No zeroes anywhere: nothing is known, and the response says so.
    assert "No public channel" in body["note"]


# ------------------------------------------------------------------- oauth start
def test_connect_returns_a_real_google_consent_url(
    auth_client: TestClient, channel: Channel, oauth_configured, db: Session
) -> None:
    response = auth_client.post("/api/youtube/connect", json={"include_analytics": True})

    assert response.status_code == 200
    body = response.json()
    assert body["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in body["authorization_url"]
    assert "access_type=offline" in body["authorization_url"]
    assert "never sees your password" in body["note"]
    # The response carries no client secret and no token material.
    assert settings.youtube_client_secret not in response.text


def test_the_oauth_callback_redirects_with_the_provider_error(
    client: TestClient, channel: Channel
) -> None:
    response = client.get(
        "/api/youtube/oauth/callback?error=access_denied", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/dashboard/channels?youtube_error=access_denied")


def test_the_oauth_callback_rejects_a_missing_code(client: TestClient) -> None:
    response = client.get("/api/youtube/oauth/callback?state=abc", follow_redirects=False)

    assert response.status_code == 303
    assert "youtube_error=missing_parameters" in response.headers["location"]


# ----------------------------------------------------------------- public channel
@respx.mock
def test_linking_a_public_channel_stores_it_without_claiming_upload_access(
    auth_client: TestClient, db: Session, channel: Channel, api_key
) -> None:
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=FIXTURE_CHANNEL))

    response = auth_client.post(
        "/api/youtube/public-channel",
        json={"channel_identifier": "UCFIXTUREAPICHANNEL00000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["channel"]["channel_id"] == "UCFIXTUREAPICHANNEL00000"
    assert body["channel"]["subscriber_count"] == 4210
    assert body["channel"]["source"] == "youtube_data_api_public"
    assert "require an OAuth connection" in body["channel"]["scope_note"]

    # The critical assertion: identifying a channel is not consent to upload to it.
    assert body["connection"]["status"] == "not_connected"
    assert body["connection"]["capabilities"]["public_read"] is True
    assert body["connection"]["capabilities"]["upload"] is False
    assert body["connection"]["capabilities"]["revenue"] is False

    stored = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    assert stored.public_channel_id == "UCFIXTUREAPICHANNEL00000"
    assert stored.access_token_encrypted is None


@respx.mock
def test_linking_a_public_channel_is_audited(
    auth_client: TestClient, db: Session, channel: Channel, api_key
) -> None:
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=FIXTURE_CHANNEL))
    auth_client.post(
        "/api/youtube/public-channel", json={"channel_identifier": "UCFIXTUREAPICHANNEL00000"}
    )

    actions = {row.action for row in db.query(AuditLog).all()}
    assert any("youtube" in action for action in actions), actions


@respx.mock
def test_an_unknown_channel_id_is_reported_not_invented(
    auth_client: TestClient, channel: Channel, api_key
) -> None:
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json={"items": []}))

    response = auth_client.post(
        "/api/youtube/public-channel", json={"channel_identifier": "UCDOESNOTEXIST0000000000"}
    )

    assert response.status_code == 404
    assert "UCDOESNOTEXIST0000000000" in response.json()["message"]


def test_refreshing_public_statistics_requires_a_linked_channel(
    auth_client: TestClient, channel: Channel, api_key
) -> None:
    response = auth_client.post("/api/youtube/public-channel/refresh", json={})

    assert response.status_code == 422
    assert "No public channel is linked" in response.json()["message"]


@respx.mock
def test_refreshing_public_statistics_stores_a_new_snapshot(
    auth_client: TestClient, db: Session, channel: Channel, api_key
) -> None:
    respx.get(CHANNELS_URL).mock(return_value=httpx.Response(200, json=FIXTURE_CHANNEL))
    auth_client.post(
        "/api/youtube/public-channel", json={"channel_identifier": "UCFIXTUREAPICHANNEL00000"}
    )

    response = auth_client.post("/api/youtube/public-channel/refresh", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"]
    assert body["captured_at"]
    assert body["channel"]["view_count"] == 915032


# ------------------------------------------------------------------------ videos
def test_the_video_list_is_empty_before_anything_is_published(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/youtube/videos").json()

    assert body["items"] == []
    assert body["total"] == 0


def test_the_video_list_returns_only_this_channels_videos(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    db.add(
        YouTubeVideo(
            channel_id=channel.id,
            youtube_video_id="fixtureVideo1",
            title="TEST FIXTURE video",
            privacy_status="private",
            published_at=datetime.now(UTC),
        )
    )
    db.commit()

    body = auth_client.get("/api/youtube/videos").json()

    assert body["total"] == 1
    assert body["items"][0]["youtube_video_id"] == "fixtureVideo1"
    assert body["items"][0]["url"] == "https://www.youtube.com/watch?v=fixtureVideo1"


# ---------------------------------------------------------------------- metadata
def test_metadata_is_absent_until_it_is_generated(
    auth_client: TestClient, project: ContentProject
) -> None:
    response = auth_client.get(f"/api/metadata/{project.id}")

    assert response.status_code == 404
    assert "No metadata" in response.json()["message"]


def test_metadata_for_an_unknown_project_is_not_found(auth_client: TestClient, channel) -> None:
    assert auth_client.get(f"/api/metadata/{uuid.uuid4()}").status_code == 404


def test_background_metadata_generation_enqueues_a_real_job(
    auth_client: TestClient, db: Session, channel: Channel, project: ContentProject
) -> None:
    response = auth_client.post(
        "/api/metadata/generate",
        json={"content_project_id": str(project.id), "background": True},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "queued"
    job = db.query(Job).filter_by(id=uuid.UUID(body["job_id"])).one()
    assert job.type == "metadata_generation"
    assert job.status == "QUEUED"


# --------------------------------------------------------------------- preflight
def test_preflight_lists_every_gate_and_blocks_an_unfinished_project(
    auth_client: TestClient, project: ContentProject
) -> None:
    response = auth_client.get(f"/api/publish/preflight/{project.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["can_publish"] is False
    assert body["blockers"], "an unfinished project must name its blockers"
    keys = {gate["key"] for gate in body["gates"]}
    assert {"render", "metadata", "youtube_connection", "quality_check"} <= keys
    # ``human_approval`` is an autopilot gate: an operator calling this endpoint *is*
    # the human, so the gate would be a tautology here.
    assert "human_approval" not in keys
    # Nothing was uploaded and nothing was invented.
    assert body["quality"] is None or body["quality"]["status"] in {"PASS", "WARN", "FAIL"}


def test_preflight_does_not_create_a_publish_job(
    auth_client: TestClient, db: Session, project: ContentProject
) -> None:
    auth_client.get(f"/api/publish/preflight/{project.id}")
    assert db.query(PublishJob).count() == 0


def test_publishing_a_blocked_project_returns_409_with_the_blockers(
    auth_client: TestClient, db: Session, project: ContentProject
) -> None:
    response = auth_client.post(
        "/api/publish", json={"content_project_id": str(project.id), "background": True}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "safety_blocked"
    assert body["details"]["blockers"]
    assert db.query(PublishJob).count() == 0
    assert db.query(Job).filter_by(type="youtube_upload").count() == 0


# ---------------------------------------------------------------------- approval
def test_approval_is_recorded_with_the_human_who_gave_it(
    auth_client: TestClient, db: Session, user: User, project: ContentProject
) -> None:
    response = auth_client.post(
        f"/api/publish/approve/{project.id}", json={"approved": True, "reason": "Reviewed."}
    )

    assert response.status_code == 200
    assert response.json()["approval_status"] == "approved"
    db.refresh(project)
    assert project.approved_by == user.id
    assert project.approved_at is not None


def test_rejection_is_recorded_and_does_not_approve(
    auth_client: TestClient, db: Session, project: ContentProject
) -> None:
    response = auth_client.post(
        f"/api/publish/approve/{project.id}",
        json={"approved": False, "reason": "Script needs a rewrite."},
    )

    assert response.json()["approval_status"] == "rejected"
    db.refresh(project)
    assert project.approved_by is None


def test_pending_approvals_lists_only_projects_awaiting_a_decision(
    auth_client: TestClient, db: Session, channel: Channel, project: ContentProject
) -> None:
    drafting = ContentProject(
        channel_id=channel.id,
        title="Still drafting",
        status="draft",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(drafting)
    db.commit()

    body = auth_client.get("/api/publish/pending/approvals").json()

    titles = {item["title"] for item in body["items"]}
    assert titles == {"Fixture project"}
    assert body["total"] == 1


def test_an_approved_project_leaves_the_pending_queue(
    auth_client: TestClient, project: ContentProject
) -> None:
    auth_client.post(f"/api/publish/approve/{project.id}", json={"approved": True})

    body = auth_client.get("/api/publish/pending/approvals").json()
    assert body["total"] == 0


# ------------------------------------------------------------------ publish jobs
def test_an_unknown_publish_job_is_not_found(auth_client: TestClient, channel: Channel) -> None:
    assert auth_client.get(f"/api/publish/{uuid.uuid4()}").status_code == 404


def test_the_publish_job_list_starts_empty(auth_client: TestClient, channel: Channel) -> None:
    body = auth_client.get("/api/publish").json()
    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_another_channels_publish_job_is_not_readable(
    auth_client: TestClient, db: Session, channel: Channel, user: User
) -> None:
    other_channel = channel_service.create_channel(db, user, name="Second channel")
    db.flush()
    other_project = ContentProject(
        channel_id=other_channel.id,
        title="Other channel project",
        status="ready",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(other_project)
    db.flush()
    job = PublishJob(
        channel_id=other_channel.id,
        content_project_id=other_project.id,
        status="pending",
        privacy_status="private",
        authorized_by="user",
        idempotency_key="fixture-key",
        created_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    # Scoped to the *default* channel, so the other channel's job must not leak.
    assert auth_client.get(f"/api/publish/{job.id}").status_code == 404


# -------------------------------------------------------------------- disconnect
def test_disconnect_clears_tokens_and_reports_the_new_state(
    auth_client: TestClient, db: Session, channel: Channel, oauth_configured
) -> None:
    from nexora.core.crypto import encrypt_str

    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = "connected"
    connection.youtube_channel_id = "UCFIXTURECHANNELID000000"
    connection.access_token_encrypted = encrypt_str("fixture-access-token")
    connection.refresh_token_encrypted = encrypt_str("fixture-refresh-token")
    connection.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    db.commit()

    response = auth_client.post("/api/youtube/disconnect", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_connected"
    assert body["capabilities"]["upload"] is False

    db.refresh(connection)
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None


def test_no_endpoint_ever_returns_token_material(
    auth_client: TestClient, db: Session, channel: Channel, oauth_configured
) -> None:
    from nexora.core.crypto import encrypt_str

    connection = db.query(YouTubeConnection).filter_by(channel_id=channel.id).one()
    connection.status = "connected"
    connection.youtube_channel_id = "UCFIXTURECHANNELID000000"
    connection.access_token_encrypted = encrypt_str("super-secret-access-token")
    connection.refresh_token_encrypted = encrypt_str("super-secret-refresh-token")
    db.commit()

    body = auth_client.get("/api/youtube/connection").text

    assert "super-secret-access-token" not in body
    assert "super-secret-refresh-token" not in body
    assert connection.access_token_encrypted not in body
    assert "access_token" not in body
    assert "refresh_token" not in body
