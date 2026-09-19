"""Asset manager.

Every byte that can reach a render is recorded with its provenance. The default
licence status is ``LICENSE UNKNOWN`` — an asset is untrusted until an operator or a
provider establishes otherwise, never the reverse.

Uploads are validated by *content*, not by filename: the declared extension is
irrelevant, the magic bytes decide.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import Channel, VideoAsset
from nexora.db.models.enums import ActorType, AssetKind, LicenseStatus
from nexora.services import audit
from nexora.services.providers.storage import get_storage

logger = get_logger(__name__)

MAX_UPLOAD_BYTES = 256 * 1024 * 1024

#: Content types we accept, keyed by the magic-byte signature that proves them.
MAGIC_SIGNATURES: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png"),
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg"),
    (b"GIF87a", 0, "image/gif", "gif"),
    (b"GIF89a", 0, "image/gif", "gif"),
    (b"RIFF", 0, "image/webp", "webp"),  # refined below by the WEBP tag at offset 8
    (b"ftyp", 4, "video/mp4", "mp4"),
    (b"\x1aE\xdf\xa3", 0, "video/webm", "webm"),
    (b"ID3", 0, "audio/mpeg", "mp3"),
    (b"\xff\xfb", 0, "audio/mpeg", "mp3"),
    (b"\xff\xf3", 0, "audio/mpeg", "mp3"),
    (b"OggS", 0, "audio/ogg", "ogg"),
    (b"fLaC", 0, "audio/flac", "flac"),
)

#: Which asset kinds a given media type may be used as.
KIND_MEDIA: dict[str, tuple[str, ...]] = {
    AssetKind.IMAGE.value: ("image/png", "image/jpeg", "image/gif", "image/webp"),
    AssetKind.THUMBNAIL.value: ("image/png", "image/jpeg", "image/webp"),
    AssetKind.VIDEO.value: ("video/mp4", "video/webm"),
    AssetKind.RENDER.value: ("video/mp4", "video/webm"),
    AssetKind.MUSIC.value: ("audio/mpeg", "audio/ogg", "audio/flac", "audio/wav"),
    AssetKind.NARRATION.value: ("audio/mpeg", "audio/ogg", "audio/flac", "audio/wav"),
}

#: Licence values an operator may declare, and whether each permits unattended use.
LICENSE_KINDS: dict[str, bool] = {
    "creator_owned": True,
    "public_domain": True,
    "cc0": True,
    "cc_by": True,
    "cc_by_sa": True,
    "licensed_stock": True,
    "ai_generated": True,
    "system_generated": True,
    "fair_use_claimed": False,
    "unknown": False,
}


@dataclass
class DetectedMedia:
    mime_type: str
    extension: str


def detect_media(head: bytes) -> DetectedMedia:
    """Identify content from its magic bytes. A filename is never trusted."""
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return DetectedMedia("image/webp", "webp")
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return DetectedMedia("audio/wav", "wav")

    for signature, offset, mime_type, extension in MAGIC_SIGNATURES:
        if head[offset : offset + len(signature)] == signature:
            return DetectedMedia(mime_type, extension)

    raise ValidationError(
        "This file's contents do not match any accepted media type. "
        "Accepted: PNG, JPEG, GIF, WebP, MP4, WebM, MP3, OGG, FLAC, WAV."
    )


def validate_license(license_type: str | None, *, status: str | None) -> tuple[str, str]:
    """Normalize a declared licence into (license_type, license_status)."""
    kind = (license_type or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    if kind not in LICENSE_KINDS:
        raise ValidationError(
            f"Unknown licence type '{license_type}'. "
            f"Supported: {', '.join(sorted(LICENSE_KINDS))}."
        )

    if status is not None:
        normalized = status.strip().upper()
        valid = {member.value for member in LicenseStatus}
        if normalized not in valid:
            raise ValidationError(f"Licence status must be one of: {', '.join(sorted(valid))}.")
        return kind, normalized

    # Derived from the licence kind rather than assumed permissive.
    return kind, (
        LicenseStatus.PERMITTED.value if LICENSE_KINDS[kind] else LicenseStatus.UNKNOWN.value
    )


def store_bytes(
    session: Session,
    channel: Channel,
    *,
    kind: str,
    data: bytes | BinaryIO,
    content_project_id: uuid.UUID | None = None,
    mime_type: str | None = None,
    extension: str | None = None,
    source: str = "upload",
    source_url: str | None = None,
    source_provider: str | None = None,
    license_type: str | None = None,
    license_status: str | None = None,
    license_url: str | None = None,
    attribution: str | None = None,
    usage_permission_note: str | None = None,
    meta: dict[str, Any] | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
    verify_media: bool = True,
) -> VideoAsset:
    """Persist bytes to object storage and record the asset with its provenance."""
    if kind not in {member.value for member in AssetKind}:
        raise ValidationError(f"Unknown asset kind '{kind}'.")

    payload = data if isinstance(data, bytes | bytearray) else data.read()
    if not payload:
        raise ValidationError("The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is {len(payload) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    if verify_media:
        detected = detect_media(bytes(payload[:32]))
        allowed = KIND_MEDIA.get(kind)
        if allowed and detected.mime_type not in allowed:
            raise ValidationError(
                f"A '{kind}' asset must be one of {', '.join(allowed)}; this file is "
                f"{detected.mime_type}."
            )
        resolved_mime, resolved_extension = detected.mime_type, detected.extension
    else:
        resolved_mime = mime_type or "application/octet-stream"
        resolved_extension = extension or "bin"

    kind_licence, status = validate_license(license_type, status=license_status)
    checksum = hashlib.sha256(payload).hexdigest()
    storage = get_storage()
    key = f"channels/{channel.id}/{kind}/{checksum[:2]}/{checksum}.{resolved_extension}"

    existing = session.execute(
        select(VideoAsset).where(
            VideoAsset.channel_id == channel.id,
            VideoAsset.checksum_sha256 == checksum,
            VideoAsset.kind == kind,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Identical bytes already stored for this channel; reuse rather than duplicate.
        logger.info("asset.deduplicated", extra={"asset_id": str(existing.id), "kind": kind})
        return existing

    stored = storage.put(key, bytes(payload), content_type=resolved_mime)
    asset = VideoAsset(
        channel_id=channel.id,
        content_project_id=content_project_id,
        kind=kind,
        source=source,
        source_url=source_url,
        source_provider=source_provider,
        license_type=kind_licence,
        license_url=license_url,
        license_status=status,
        attribution=attribution,
        usage_permission_note=usage_permission_note,
        acquired_at=datetime.now(UTC),
        storage_backend=stored.backend,
        storage_key=stored.key,
        mime_type=resolved_mime,
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum_sha256,
        meta=meta or {},
    )
    session.add(asset)
    session.flush()

    _measure(asset)
    session.flush()

    audit.record(
        session,
        action="asset.created",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="video_asset",
        entity_id=asset.id,
        summary=(
            f"Stored {kind} asset ({resolved_mime}, {stored.size_bytes} bytes), "
            f"licence {kind_licence} → {status}."
        ),
    )
    return asset


def _measure(asset: VideoAsset) -> None:
    """Measure duration and dimensions from the stored file where possible."""
    from pathlib import Path

    storage = get_storage()
    local = storage.local_path(asset.storage_key)
    if not local or not Path(local).is_file():
        return

    if asset.kind in (AssetKind.IMAGE.value, AssetKind.THUMBNAIL.value):
        try:
            from PIL import Image

            with Image.open(local) as image:
                asset.width, asset.height = image.size
        except Exception as exc:  # a measurement failure must not lose the asset
            logger.info("asset.image_measure_failed", extra={"error": str(exc)})
        return

    from nexora.services.ffmpeg_runtime import probe_duration_seconds

    duration = probe_duration_seconds(Path(local))
    if duration is not None:
        asset.duration_seconds = duration


def get_asset(session: Session, channel_id: uuid.UUID, asset_id: uuid.UUID) -> VideoAsset:
    asset = session.get(VideoAsset, asset_id)
    if asset is None or asset.channel_id != channel_id:
        raise NotFound("Asset not found.")
    return asset


def list_assets(
    session: Session,
    channel_id: uuid.UUID,
    *,
    kind: str | None = None,
    content_project_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[VideoAsset]:
    stmt = select(VideoAsset).where(VideoAsset.channel_id == channel_id)
    if kind:
        stmt = stmt.where(VideoAsset.kind == kind)
    if content_project_id:
        stmt = stmt.where(VideoAsset.content_project_id == content_project_id)
    return list(
        session.execute(
            stmt.order_by(VideoAsset.created_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )


def set_license(
    session: Session,
    asset: VideoAsset,
    *,
    license_type: str | None,
    license_status: str | None = None,
    license_url: str | None = None,
    attribution: str | None = None,
    note: str | None = None,
    user_id: uuid.UUID,
) -> VideoAsset:
    """Record an operator's licence determination for an asset."""
    before = asset_to_dict(asset)
    kind, status = validate_license(license_type, status=license_status)
    asset.license_type = kind
    asset.license_status = status
    asset.license_url = license_url
    asset.attribution = attribution
    asset.usage_permission_note = note
    session.flush()

    audit.record(
        session,
        action="asset.license_updated",
        user_id=user_id,
        channel_id=asset.channel_id,
        entity_type="video_asset",
        entity_id=asset.id,
        summary=f"Licence set to {kind} → {status}",
        before=before,
        after=asset_to_dict(asset),
    )
    return asset


def open_asset(asset: VideoAsset):
    """Stream an asset's bytes from storage."""
    return get_storage().stream(asset.storage_key)


def local_path_for(asset: VideoAsset) -> str | None:
    """Filesystem path when the backend has one, so FFmpeg can read without a copy."""
    return get_storage().local_path(asset.storage_key)


def asset_to_dict(asset: VideoAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "kind": asset.kind,
        "source": asset.source,
        "source_url": asset.source_url,
        "source_provider": asset.source_provider,
        "license_type": asset.license_type,
        "license_status": asset.license_status,
        "license_url": asset.license_url,
        "attribution": asset.attribution,
        "usage_permission_note": asset.usage_permission_note,
        "acquired_at": asset.acquired_at.isoformat() if asset.acquired_at else None,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "checksum_sha256": asset.checksum_sha256,
        "width": asset.width,
        "height": asset.height,
        "duration_seconds": asset.duration_seconds,
        "content_project_id": (
            str(asset.content_project_id) if asset.content_project_id else None
        ),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "blocks_autonomous_publishing": asset.license_status != LicenseStatus.PERMITTED.value,
    }
