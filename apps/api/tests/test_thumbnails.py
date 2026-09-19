"""Thumbnail composition, licence inheritance and approval."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from nexora.core.errors import ValidationError
from nexora.db.models import AuditLog, Channel, ContentProject, Thumbnail, VideoAsset
from nexora.db.models.enums import LicenseStatus
from nexora.services import assets as asset_service
from nexora.services import thumbnails as thumbnail_service


@pytest.fixture
def project(db: Session, channel: Channel) -> ContentProject:
    row = ContentProject(
        channel_id=channel.id,
        title="Why the wafer-start numbers disagree, and what both sides measured",
        status="ready",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(row)
    db.commit()
    return row


def make_png(size: tuple[int, int] = (800, 600), colour: str = "#336699") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_compose_produces_a_real_youtube_sized_image() -> None:
    data = thumbnail_service.compose(
        headline="Why the wafer-start numbers disagree",
        palette=thumbnail_service.DEFAULT_PALETTE,
        eyebrow="long form",
    )
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1280, 720)
        assert image.mode == "RGB"
        bright = sum(1 for value in image.convert("L").getdata() if value > 150)
    assert bright > 2000, "no text appears to have been drawn"
    assert len(data) <= thumbnail_service.MAX_THUMBNAIL_BYTES


def test_compose_requires_a_headline() -> None:
    with pytest.raises(ValidationError):
        thumbnail_service.compose(headline="   ", palette=thumbnail_service.DEFAULT_PALETTE)


def test_a_very_long_headline_still_fits() -> None:
    data = thumbnail_service.compose(
        headline="A considerably longer headline than usual that has to be wrapped across "
        "several lines without overflowing the canvas or clipping",
        palette=thumbnail_service.DEFAULT_PALETTE,
    )
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1280, 720)


def test_invalid_brand_colours_are_rejected() -> None:
    with pytest.raises(ValidationError):
        thumbnail_service.compose(
            headline="Test", palette={**thumbnail_service.DEFAULT_PALETTE, "accent": "not-a-colour"}
        )


def test_generate_stores_an_asset_and_awaits_a_decision(
    db: Session, channel: Channel, project
) -> None:
    result = thumbnail_service.generate(db, channel, project)
    db.commit()

    assert result.thumbnail.status == "generated", "a thumbnail is never auto-approved"
    assert (result.thumbnail.width, result.thumbnail.height) == (1280, 720)
    assert result.asset.kind == "thumbnail"
    assert result.asset.mime_type == "image/png"
    assert result.asset.license_status == LicenseStatus.PERMITTED.value
    assert project.current_thumbnail_id is None, "not current until approved"


def test_the_output_never_claims_performance(db: Session, channel: Channel, project) -> None:
    payload = thumbnail_service.generate(db, channel, project).to_dict()
    text = str(payload).lower()

    # The disclaimer must be present; it legitimately contains "will perform".
    assert "makes no claim about how it will perform" in text

    # What must never appear is a phrase asserting an outcome.
    for claim in (
        "estimated ctr",
        "expected ctr",
        "predicted click",
        "expected clicks",
        "guaranteed",
        "will increase",
        "high-performing",
    ):
        assert claim not in text
    # No bare performance metric is reported at all.
    assert "ctr" not in payload


def test_a_permitted_background_keeps_the_thumbnail_permitted(
    db: Session, channel: Channel, project
) -> None:
    background = asset_service.store_bytes(
        db, channel, kind="image", data=make_png(), license_type="creator_owned"
    )
    db.commit()

    result = thumbnail_service.generate(
        db, channel, project, background_asset_id=background.id
    )
    db.commit()
    assert result.asset.license_status == LicenseStatus.PERMITTED.value
    assert result.warnings == []


def test_an_unknown_licence_background_is_inherited_and_warned_about(
    db: Session, channel: Channel, project
) -> None:
    """The composite is only as permitted as its least permitted input."""
    background = asset_service.store_bytes(
        db, channel, kind="image", data=make_png(colour="#993366")
    )
    db.commit()
    assert background.license_status == LicenseStatus.UNKNOWN.value

    result = thumbnail_service.generate(
        db, channel, project, background_asset_id=background.id
    )
    db.commit()

    assert result.asset.license_status == LicenseStatus.UNKNOWN.value
    assert asset_service.asset_to_dict(result.asset)["blocks_autonomous_publishing"] is True
    assert any("block autonomous publishing" in warning for warning in result.warnings)


def test_a_non_image_background_is_refused(db: Session, channel: Channel, project) -> None:
    from tests.media_helpers import ffmpeg_available, make_tone_mp3

    if not ffmpeg_available():
        pytest.skip("FFmpeg is not installed")
    audio = asset_service.store_bytes(
        db, channel, kind="music", data=make_tone_mp3(seconds=1.0), license_type="creator_owned"
    )
    db.commit()
    with pytest.raises(ValidationError):
        thumbnail_service.generate(db, channel, project, background_asset_id=audio.id)


def test_approval_sets_the_project_thumbnail(db: Session, channel: Channel, project, user) -> None:
    result = thumbnail_service.generate(db, channel, project)
    db.commit()

    thumbnail_service.decide(db, result.thumbnail, approved=True, user_id=user.id)
    db.commit()

    assert result.thumbnail.status == "approved"
    assert project.current_thumbnail_id == result.thumbnail.id
    assert db.query(AuditLog).filter(AuditLog.action == "thumbnail.approved").count() == 1


def test_rejection_does_not_set_the_project_thumbnail(
    db: Session, channel: Channel, project, user
) -> None:
    result = thumbnail_service.generate(db, channel, project)
    db.commit()
    thumbnail_service.decide(db, result.thumbnail, approved=False, user_id=user.id)
    db.commit()

    assert result.thumbnail.status == "rejected"
    assert project.current_thumbnail_id is None


def test_regenerating_keeps_every_candidate(db: Session, channel: Channel, project) -> None:
    thumbnail_service.generate(db, channel, project, headline="First idea")
    db.commit()
    thumbnail_service.generate(db, channel, project, headline="Second idea")
    db.commit()

    rows = thumbnail_service.list_for_project(db, project.id)
    assert len(rows) == 2
    assert {row.headline for row in rows} == {"First idea", "Second idea"}
    assert db.query(Thumbnail).count() == 2
    assert db.query(VideoAsset).filter(VideoAsset.kind == "thumbnail").count() == 2
