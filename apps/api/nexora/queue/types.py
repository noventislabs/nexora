"""Job type constants and the handler registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from nexora.db.models import Job

TREND_SCAN = "trend_scan"
TOPIC_GENERATION = "topic_generation"
RESEARCH = "research"
SCRIPT_GENERATION = "script_generation"
FACT_CHECK = "fact_check"
VOICE_GENERATION = "voice_generation"
ASSET_COLLECTION = "asset_collection"
VIDEO_RENDER = "video_render"
THUMBNAIL_GENERATION = "thumbnail_generation"
METADATA_GENERATION = "metadata_generation"
QUALITY_CHECK = "quality_check"
YOUTUBE_UPLOAD = "youtube_upload"
ANALYTICS_SYNC = "analytics_sync"
AUTOMATION_TICK = "automation_tick"
AUTOMATION_ADVANCE = "automation_advance"

ALL_JOB_TYPES = (
    TREND_SCAN,
    TOPIC_GENERATION,
    RESEARCH,
    SCRIPT_GENERATION,
    FACT_CHECK,
    VOICE_GENERATION,
    ASSET_COLLECTION,
    VIDEO_RENDER,
    THUMBNAIL_GENERATION,
    METADATA_GENERATION,
    QUALITY_CHECK,
    YOUTUBE_UPLOAD,
    ANALYTICS_SYNC,
    AUTOMATION_TICK,
    AUTOMATION_ADVANCE,
)

#: Long-running media work gets a generous ceiling; everything else is bounded tightly.
JOB_TIMEOUTS: dict[str, int] = {
    VIDEO_RENDER: 3600,
    VOICE_GENERATION: 900,
    YOUTUBE_UPLOAD: 1800,
}
DEFAULT_JOB_TIMEOUT = 600

JobHandler = Callable[["Session", "Job"], dict[str, Any] | None]

_handlers: dict[str, JobHandler] = {}


@dataclass(frozen=True)
class HandlerInfo:
    type: str
    handler: JobHandler


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    if job_type not in ALL_JOB_TYPES:
        raise ValueError(f"Unknown job type '{job_type}'.")

    def decorator(func: JobHandler) -> JobHandler:
        _handlers[job_type] = func
        return func

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    return _handlers.get(job_type)


def registered_types() -> list[str]:
    return sorted(_handlers)
