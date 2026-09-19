"""Asset manager: content-based validation, licence provenance and storage."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session

from nexora.core.errors import ValidationError
from nexora.db.models import AuditLog, Channel, VideoAsset
from nexora.db.models.enums import LicenseStatus
from nexora.services import assets as asset_service
from tests.fixtures import feeds


def test_media_is_detected_from_magic_bytes_not_the_filename() -> None:
    assert asset_service.detect_media(feeds.TINY_PNG).mime_type == "image/png"
    assert asset_service.detect_media(b"\xff\xd8\xff\xe0" + b"\x00" * 20).mime_type == "image/jpeg"
    assert asset_service.detect_media(b"\x00\x00\x00 ftypisom" + b"\x00" * 16).mime_type == "video/mp4"
    assert asset_service.detect_media(b"RIFF\x00\x00\x00\x00WAVEfmt ").mime_type == "audio/wav"
    assert asset_service.detect_media(b"RIFF\x00\x00\x00\x00WEBPVP8 ").mime_type == "image/webp"


def test_a_script_disguised_as_an_image_is_refused() -> None:
    """A .png filename means nothing; the bytes decide."""
    for payload in (b"<?php system($_GET[0]); ?>", b"#!/bin/sh\nrm -rf /", b"<svg onload=alert(1)>"):
        with pytest.raises(ValidationError):
            asset_service.detect_media(payload)


def test_licence_defaults_to_unknown_not_permitted() -> None:
    assert asset_service.validate_license(None, status=None) == (
        "unknown",
        LicenseStatus.UNKNOWN.value,
    )
    assert asset_service.validate_license("fair_use_claimed", status=None)[1] == (
        LicenseStatus.UNKNOWN.value
    )
    assert asset_service.validate_license("public_domain", status=None)[1] == (
        LicenseStatus.PERMITTED.value
    )


def test_unknown_licence_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        asset_service.validate_license("probably-fine", status=None)


def test_store_records_provenance_and_checksum(db: Session, channel: Channel) -> None:
    asset = asset_service.store_bytes(
        db,
        channel,
        kind="image",
        data=feeds.TINY_PNG,
        source="upload",
        source_url="https://fixture.invalid/image.png",
        license_type="cc_by",
        attribution="Fixture Author",
    )
    db.commit()

    assert asset.mime_type == "image/png"
    assert asset.checksum_sha256 == hashlib.sha256(feeds.TINY_PNG).hexdigest()
    assert asset.license_type == "cc_by"
    assert asset.license_status == LicenseStatus.PERMITTED.value
    assert asset.attribution == "Fixture Author"
    assert asset.acquired_at is not None
    assert asset.storage_key.startswith(f"channels/{channel.id}/image/")
    # A 1x1 PNG: dimensions are measured from the file, not declared.
    assert (asset.width, asset.height) == (1, 1)


def test_unknown_licence_blocks_autonomous_publishing(db: Session, channel: Channel) -> None:
    asset = asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    db.commit()
    payload = asset_service.asset_to_dict(asset)
    assert payload["license_status"] == "LICENSE UNKNOWN"
    assert payload["blocks_autonomous_publishing"] is True


def test_identical_bytes_are_stored_once(db: Session, channel: Channel) -> None:
    first = asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    db.commit()
    second = asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    db.commit()
    assert first.id == second.id
    assert db.query(VideoAsset).count() == 1


def test_kind_and_media_type_must_agree(db: Session, channel: Channel) -> None:
    with pytest.raises(ValidationError) as exc:
        asset_service.store_bytes(db, channel, kind="video", data=feeds.TINY_PNG)
    assert "must be one of" in exc.value.message


def test_empty_and_oversized_uploads_are_refused(db: Session, channel: Channel, monkeypatch) -> None:
    with pytest.raises(ValidationError):
        asset_service.store_bytes(db, channel, kind="image", data=b"")

    monkeypatch.setattr(asset_service, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValidationError) as exc:
        asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    assert "limit" in exc.value.message


def test_setting_a_licence_is_audited(db: Session, channel: Channel, user) -> None:
    asset = asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    db.commit()
    asset_service.set_license(
        db, asset, license_type="creator_owned", attribution="Us", user_id=user.id
    )
    db.commit()

    assert asset.license_status == LicenseStatus.PERMITTED.value
    entry = db.query(AuditLog).filter(AuditLog.action == "asset.license_updated").one()
    assert "creator_owned" in (entry.summary or "")


def test_stored_bytes_round_trip(db: Session, channel: Channel) -> None:
    asset = asset_service.store_bytes(db, channel, kind="image", data=feeds.TINY_PNG)
    db.commit()
    assert b"".join(asset_service.open_asset(asset)) == feeds.TINY_PNG
