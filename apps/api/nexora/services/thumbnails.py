"""Thumbnail generation.

Composites a thumbnail from the channel's own branding and, optionally, an image asset
the operator has supplied. Output is a real PNG at YouTube's recommended 1280x720.

Two things this deliberately does not do:

* It never predicts or implies a click-through rate. A thumbnail is a design artefact,
  and no property of this system can know how it will perform.
* It never uses an image whose licence is unknown without saying so — the generated
  thumbnail inherits the most restrictive licence status of its inputs.
"""

from __future__ import annotations

import io
import textwrap
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import NotFound, ValidationError
from nexora.core.logging import get_logger
from nexora.db.models import Channel, ContentProject, Thumbnail, VideoAsset
from nexora.db.models.enums import ActorType, AssetKind, LicenseStatus
from nexora.services import assets as asset_service
from nexora.services import audit

logger = get_logger(__name__)

#: YouTube's recommended thumbnail size, and its hard upper bound on file size.
WIDTH, HEIGHT = 1280, 720
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024

FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

DEFAULT_PALETTE = {"background": "#0B1017", "surface": "#10161F", "accent": "#22D3EE"}


@dataclass
class ThumbnailResult:
    thumbnail: Thumbnail
    asset: VideoAsset
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "thumbnail_id": str(self.thumbnail.id),
            "asset_id": str(self.asset.id),
            "width": self.thumbnail.width,
            "height": self.thumbnail.height,
            "size_bytes": self.asset.size_bytes,
            "generator": self.thumbnail.generator,
            "license_status": self.asset.license_status,
            "warnings": self.warnings,
            "note": (
                "A thumbnail is a design artefact. NEXORA makes no claim about how it "
                "will perform."
            ),
        }


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise ValidationError(
        "No usable font was found for thumbnail text. Install DejaVu or Liberation fonts."
    )


def _hex_to_rgb(colour: str) -> tuple[int, int, int]:
    value = (colour or "").strip().lstrip("#")
    if len(value) != 6:
        raise ValidationError(f"'{colour}' is not a six-digit hex colour.")
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        raise ValidationError(f"'{colour}' is not a six-digit hex colour.") from None


def palette_for(channel: Channel) -> dict[str, str]:
    branding = channel.branding or {}
    palette = {**DEFAULT_PALETTE}
    for key in palette:
        candidate = branding.get(key)
        if isinstance(candidate, str) and candidate.strip():
            _hex_to_rgb(candidate)
            palette[key] = candidate
    return palette


def _gradient(size: tuple[int, int], start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    """A diagonal gradient, drawn rather than sampled from any external image."""
    width, height = size
    base = Image.new("RGB", (width, height), start)
    overlay = Image.new("L", (width, height))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, width, 4):
        alpha = int(255 * (x / max(width - 1, 1)))
        draw.rectangle([x, 0, x + 4, height], fill=alpha)
    return Image.composite(Image.new("RGB", (width, height), end), base, overlay)


def compose(
    *,
    headline: str,
    palette: dict[str, str],
    eyebrow: str | None = None,
    background: Image.Image | None = None,
) -> bytes:
    """Render the thumbnail image and return PNG bytes."""
    text = (headline or "").strip()
    if not text:
        raise ValidationError("A thumbnail needs a headline.")

    accent = _hex_to_rgb(palette["accent"])
    canvas = _gradient(
        (WIDTH, HEIGHT), _hex_to_rgb(palette["background"]), _hex_to_rgb(palette["surface"])
    )

    if background is not None:
        # Cover-fit the supplied image, then darken it so text stays legible.
        source = background.convert("RGB")
        scale = max(WIDTH / source.width, HEIGHT / source.height)
        resized = source.resize(
            (max(1, int(source.width * scale)), max(1, int(source.height * scale))),
            Image.LANCZOS,
        )
        left = (resized.width - WIDTH) // 2
        top = (resized.height - HEIGHT) // 2
        cropped = resized.crop((left, top, left + WIDTH, top + HEIGHT))
        cropped = cropped.filter(ImageFilter.GaussianBlur(radius=3))
        canvas = Image.blend(canvas, cropped, alpha=0.55)
        shade = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        canvas = Image.blend(canvas, shade, alpha=0.35)

    draw = ImageDraw.Draw(canvas)

    # Accent rule, anchoring the composition.
    draw.rectangle([72, HEIGHT - 96, 72 + 220, HEIGHT - 88], fill=accent)

    if eyebrow:
        eyebrow_font = _load_font(FONT_CANDIDATES_REGULAR, 30)
        draw.text((72, 84), eyebrow.strip().upper()[:48], font=eyebrow_font, fill=accent)

    # Fit the headline: try decreasing sizes until it fits the text box.
    box_width = WIDTH - 144
    box_height = HEIGHT - 300
    for size in (104, 92, 80, 70, 62, 54, 48):
        font = _load_font(FONT_CANDIDATES_BOLD, size)
        wrap_at = max(10, int(box_width / (size * 0.56)))
        lines = textwrap.wrap(text, width=wrap_at)[:4]
        line_height = int(size * 1.16)
        if lines and len(lines) * line_height <= box_height:
            break
    else:  # pragma: no cover - the smallest size always fits four lines
        font = _load_font(FONT_CANDIDATES_BOLD, 48)
        lines = textwrap.wrap(text, width=28)[:4]
        line_height = int(48 * 1.16)

    y = 168
    for line in lines:
        # A soft shadow keeps the text readable over any background.
        draw.text((74, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((72, y), line, font=font, fill=(240, 245, 250))
        y += line_height

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()

    if len(data) > MAX_THUMBNAIL_BYTES:
        # Fall back to JPEG quality steps rather than silently shipping an oversized file.
        for quality in (92, 85, 78, 70):
            buffer = io.BytesIO()
            canvas.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= MAX_THUMBNAIL_BYTES:
                break
        else:
            raise ValidationError(
                "The generated thumbnail exceeds YouTube's 2 MB limit even at low quality."
            )
    return data


def generate(
    session: Session,
    channel: Channel,
    project: ContentProject,
    *,
    headline: str | None = None,
    eyebrow: str | None = None,
    background_asset_id: uuid.UUID | None = None,
    concept: str | None = None,
    user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
) -> ThumbnailResult:
    """Generate a thumbnail for a project and record it awaiting a human decision."""
    text = (headline or project.title or "").strip()
    if not text:
        raise ValidationError("A thumbnail needs a headline.")

    warnings: list[str] = []
    background: Image.Image | None = None
    background_asset: VideoAsset | None = None

    if background_asset_id is not None:
        background_asset = asset_service.get_asset(session, channel.id, background_asset_id)
        if background_asset.kind not in (AssetKind.IMAGE.value, AssetKind.THUMBNAIL.value):
            raise ValidationError("The background asset must be an image.")
        try:
            background = Image.open(io.BytesIO(b"".join(asset_service.open_asset(background_asset))))
            background.load()
        except Exception as exc:
            raise ValidationError(f"The background image could not be read: {exc}") from exc

        if background_asset.license_status != LicenseStatus.PERMITTED.value:
            warnings.append(
                f"The background image's licence is '{background_asset.license_status}', so "
                "this thumbnail inherits that status and will block autonomous publishing."
            )

    data = compose(
        headline=text,
        palette=palette_for(channel),
        eyebrow=eyebrow or project.video_format.replace("_", " "),
        background=background,
    )

    # The composite is only as permitted as its least permitted input.
    license_type = "system_generated"
    license_status = LicenseStatus.PERMITTED.value
    if background_asset is not None and background_asset.license_status != LicenseStatus.PERMITTED.value:
        license_type = background_asset.license_type or "unknown"
        license_status = background_asset.license_status

    asset = asset_service.store_bytes(
        session,
        channel,
        kind=AssetKind.THUMBNAIL.value,
        data=data,
        content_project_id=project.id,
        source="system",
        source_provider="nexora",
        license_type=license_type,
        license_status=license_status,
        usage_permission_note=(
            "Composited by NEXORA from channel branding"
            + (" and an operator-supplied background image." if background_asset else ".")
        ),
        meta={
            "headline": text[:200],
            "background_asset_id": str(background_asset.id) if background_asset else None,
        },
        user_id=user_id,
        actor_type=actor_type,
    )

    thumbnail = Thumbnail(
        content_project_id=project.id,
        asset_id=asset.id,
        generator="composite",
        concept=concept,
        headline=text[:120],
        status="generated",
        width=asset.width or WIDTH,
        height=asset.height or HEIGHT,
        created_at=datetime.now(UTC),
    )
    session.add(thumbnail)
    session.flush()

    audit.record(
        session,
        action="thumbnail.generated",
        actor_type=actor_type,
        user_id=user_id,
        channel_id=channel.id,
        entity_type="thumbnail",
        entity_id=thumbnail.id,
        summary=f"Generated thumbnail '{text[:80]}' ({asset.size_bytes} bytes, {license_status}).",
    )
    logger.info(
        "thumbnail.generated",
        extra={"project_id": str(project.id), "bytes": asset.size_bytes},
    )
    return ThumbnailResult(thumbnail=thumbnail, asset=asset, warnings=warnings)


def decide(
    session: Session,
    thumbnail: Thumbnail,
    *,
    approved: bool,
    user_id: uuid.UUID,
) -> Thumbnail:
    thumbnail.status = "approved" if approved else "rejected"
    thumbnail.decided_at = datetime.now(UTC)
    thumbnail.decided_by = user_id
    session.flush()

    if approved:
        project = session.get(ContentProject, thumbnail.content_project_id)
        if project is not None:
            project.current_thumbnail_id = thumbnail.id

    audit.record(
        session,
        action=f"thumbnail.{thumbnail.status}",
        user_id=user_id,
        channel_id=session.get(ContentProject, thumbnail.content_project_id).channel_id,
        entity_type="thumbnail",
        entity_id=thumbnail.id,
        summary=f"Thumbnail {thumbnail.status}: {thumbnail.headline or ''}"[:200],
    )
    return thumbnail


def list_for_project(session: Session, project_id: uuid.UUID) -> list[Thumbnail]:
    return list(
        session.execute(
            select(Thumbnail)
            .where(Thumbnail.content_project_id == project_id)
            .order_by(Thumbnail.created_at.desc())
        ).scalars()
    )


def get_thumbnail(session: Session, project_id: uuid.UUID, thumbnail_id: uuid.UUID) -> Thumbnail:
    thumbnail = session.get(Thumbnail, thumbnail_id)
    if thumbnail is None or thumbnail.content_project_id != project_id:
        raise NotFound("Thumbnail not found.")
    return thumbnail


def thumbnail_to_dict(thumbnail: Thumbnail, asset: VideoAsset | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(thumbnail.id),
        "asset_id": str(thumbnail.asset_id) if thumbnail.asset_id else None,
        "generator": thumbnail.generator,
        "concept": thumbnail.concept,
        "headline": thumbnail.headline,
        "status": thumbnail.status,
        "width": thumbnail.width,
        "height": thumbnail.height,
        "error": thumbnail.error,
        "created_at": thumbnail.created_at.isoformat() if thumbnail.created_at else None,
        "decided_at": thumbnail.decided_at.isoformat() if thumbnail.decided_at else None,
    }
    if asset is not None:
        payload["license_status"] = asset.license_status
        payload["size_bytes"] = asset.size_bytes
        payload["mime_type"] = asset.mime_type
    return payload
