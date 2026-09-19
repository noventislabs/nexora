"""Media API: authorization, NOT_CONFIGURED paths, uploads and streaming."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from nexora.db.models import Channel, ContentProject, Job
from nexora.services import auth as auth_service
from nexora.services import channels as channel_service
from tests.conftest import TEST_PASSWORD


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


def png_bytes(colour: str = "#224466") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------------------------------------------------- authorization
def test_every_media_endpoint_requires_authentication(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/voice/status"),
        ("post", "/api/voice/generate"),
        ("get", "/api/assets"),
        ("post", "/api/assets"),
        ("get", "/api/video/capability"),
        ("post", "/api/video/render"),
        ("post", "/api/thumbnails/generate"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_another_users_assets_are_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    other_channel = channel_service.create_channel(db, other, name="Other")
    db.commit()
    assert auth_client.get(f"/api/assets?channel_id={other_channel.id}").status_code == 403


def test_media_writes_require_csrf(auth_client: TestClient, channel: Channel, project) -> None:
    auth_client.headers.pop(auth_service.CSRF_HEADER)
    response = auth_client.post(
        "/api/voice/generate", json={"content_project_id": str(project.id)}
    )
    assert response.status_code == 403


# ----------------------------------------------------------------- not configured
def test_voice_status_reports_not_configured(auth_client: TestClient) -> None:
    body = auth_client.get("/api/voice/status").json()
    assert body["status"] == "NOT CONFIGURED"
    assert body["missing_settings"] == ["VOICE_PROVIDER"]
    assert body["voices"] == []


def test_voice_generation_without_a_provider_returns_503(
    auth_client: TestClient, db: Session, channel: Channel, project
) -> None:
    """NOT_CONFIGURED is reported even before the workflow is ready.

    The project below has no script version, so there are two reasons this cannot run.
    The missing provider is the deployment-level one, and it is the one reported.
    """
    response = auth_client.post(
        "/api/voice/generate",
        json={"content_project_id": str(project.id), "background": False},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "provider_not_configured"
    assert "VOICE PROVIDER NOT CONFIGURED" in response.json()["message"]


def test_voice_job_without_a_provider_fails_permanently(
    auth_client: TestClient, db: Session, channel: Channel, project
) -> None:
    from datetime import UTC, datetime

    from nexora.db.models import ContentScript, ScriptVersion

    script = ContentScript(content_project_id=project.id, current_version=1)
    db.add(script)
    db.flush()
    version = ScriptVersion(
        script_id=script.id,
        version=1,
        sections=[],
        plain_text="x",
        narration_text="Some narration text.",
        word_count=3,
        estimated_duration_seconds=2,
        created_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    project.current_script_version_id = version.id
    db.commit()

    auth_client.post(
        "/api/voice/generate",
        json={"content_project_id": str(project.id), "background": True},
    )
    db.commit()

    from nexora.queue.worker import Worker

    assert Worker().run_once() is True
    db.expire_all()
    job = db.query(Job).filter(Job.type == "voice_generation").one()
    assert job.status == "FAILED"
    assert job.permanent_failure is True, "a missing key is not a transient fault"
    assert "NOT CONFIGURED" in (job.error or "")


def test_render_capability_reports_the_real_toolchain(auth_client: TestClient) -> None:
    from nexora.services.ffmpeg_runtime import ffmpeg_info

    body = auth_client.get("/api/video/capability").json()
    assert body["status"] == ("AVAILABLE" if ffmpeg_info().available else "UNAVAILABLE")
    assert body["provider"] == "ffmpeg"


# ---------------------------------------------------------------------- uploads
def test_uploading_an_asset_records_provenance(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    response = auth_client.post(
        "/api/assets",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data={
            "kind": "image",
            "license_type": "cc_by",
            "attribution": "Fixture Author",
            "source_url": "https://fixture.invalid/photo",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mime_type"] == "image/png"
    assert body["license_status"] == "PERMITTED"
    assert body["blocks_autonomous_publishing"] is False
    assert body["width"] == 64


def test_upload_defaults_to_unknown_licence(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    body = auth_client.post(
        "/api/assets",
        files={"file": ("photo.png", png_bytes("#884422"), "image/png")},
        data={"kind": "image"},
    ).json()
    assert body["license_status"] == "LICENSE UNKNOWN"
    assert body["blocks_autonomous_publishing"] is True


def test_a_disguised_script_upload_is_refused(
    auth_client: TestClient, channel: Channel
) -> None:
    """The declared filename and content-type both say PNG; the bytes do not."""
    response = auth_client.post(
        "/api/assets",
        files={"file": ("innocent.png", b"<?php system($_GET[0]); ?>", "image/png")},
        data={"kind": "image"},
    )
    assert response.status_code == 422
    assert "do not match any accepted media type" in response.json()["message"]


def test_asset_download_is_served_as_an_attachment(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    created = auth_client.post(
        "/api/assets",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data={"kind": "image", "license_type": "creator_owned"},
    ).json()

    response = auth_client.get(f"/api/assets/{created['id']}/content")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == png_bytes()


def test_licence_can_be_corrected_later(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    created = auth_client.post(
        "/api/assets",
        files={"file": ("photo.png", png_bytes("#112233"), "image/png")},
        data={"kind": "image"},
    ).json()
    assert created["license_status"] == "LICENSE UNKNOWN"

    updated = auth_client.patch(
        f"/api/assets/{created['id']}/license",
        json={"license_type": "licensed_stock", "license_url": "https://fixture.invalid/licence"},
    )
    assert updated.status_code == 200
    assert updated.json()["license_status"] == "PERMITTED"
    assert updated.json()["blocks_autonomous_publishing"] is False


def test_an_unknown_licence_type_is_rejected(
    auth_client: TestClient, channel: Channel
) -> None:
    created = auth_client.post(
        "/api/assets",
        files={"file": ("photo.png", png_bytes("#445566"), "image/png")},
        data={"kind": "image"},
    ).json()
    response = auth_client.patch(
        f"/api/assets/{created['id']}/license", json={"license_type": "probably-fine"}
    )
    assert response.status_code == 422


def test_asset_listing_exposes_the_vocabularies(auth_client: TestClient, channel: Channel) -> None:
    body = auth_client.get("/api/assets").json()
    assert "thumbnail" in body["kinds"]
    assert "public_domain" in body["license_types"]
    assert "unknown" in body["license_types"]


# ------------------------------------------------------------------- thumbnails
def test_thumbnail_generation_and_approval_through_the_api(
    auth_client: TestClient, db: Session, channel: Channel, project
) -> None:
    generated = auth_client.post(
        "/api/thumbnails/generate",
        json={"content_project_id": str(project.id), "headline": "A clear headline", "background": False},
    )
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["width"] == 1280 and body["height"] == 720
    assert body["license_status"] == "PERMITTED"

    listing = auth_client.get(f"/api/thumbnails/{project.id}").json()
    assert listing["total"] == 1
    assert listing["current_thumbnail_id"] is None

    decided = auth_client.post(
        f"/api/thumbnails/{project.id}/{body['thumbnail_id']}/decision", json={"approved": True}
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    listing = auth_client.get(f"/api/thumbnails/{project.id}").json()
    assert listing["current_thumbnail_id"] == body["thumbnail_id"]


def test_thumbnail_for_a_missing_project_is_a_404(auth_client: TestClient, channel: Channel) -> None:
    response = auth_client.post(
        "/api/thumbnails/generate",
        json={"content_project_id": str(uuid.uuid4()), "background": False},
    )
    assert response.status_code == 404


def test_render_listing_is_empty_before_any_render(
    auth_client: TestClient, channel: Channel, project
) -> None:
    body = auth_client.get(f"/api/video/{project.id}/renders").json()
    assert body == {"items": [], "total": 0, "scene_plan": []}
