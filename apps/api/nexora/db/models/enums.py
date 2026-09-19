"""Enumerations shared by models and the API layer.

Values are persisted as plain strings rather than native PostgreSQL enums so that
adding a state is an application-level change instead of a locking DDL migration.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"
    OPERATOR = "operator"
    VIEWER = "viewer"


class AutomationMode(StrEnum):
    ASSISTED = "assisted"
    SEMI_AUTONOMOUS = "semi_autonomous"
    AUTONOMOUS = "autonomous"


class ConnectionStatus(StrEnum):
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class TrendSourceKind(StrEnum):
    YOUTUBE_DATA_API = "youtube_data_api"
    RSS = "rss"
    REDDIT = "reddit"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SAVED = "saved"
    CONVERTED = "converted"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    RESEARCHING = "researching"
    SCRIPTING = "scripting"
    FACT_CHECK = "fact_check"
    VOICE = "voice"
    ASSETS = "assets"
    RENDERING = "rendering"
    READY = "ready"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class VideoFormat(StrEnum):
    LONG_FORM = "long_form"
    SHORT = "short"


class ClaimClassification(StrEnum):
    FACT = "FACT"
    CLAIM = "CLAIM"
    ANALYSIS = "ANALYSIS"
    OPINION = "OPINION"
    UNKNOWN = "UNKNOWN"


class CheckStatus(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class LicenseStatus(StrEnum):
    PERMITTED = "PERMITTED"
    UNKNOWN = "LICENSE UNKNOWN"
    PROHIBITED = "PROHIBITED"


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    MUSIC = "music"
    NARRATION = "narration"
    SUBTITLE = "subtitle"
    RENDER = "render"
    THUMBNAIL = "thumbnail"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublishAuthorization(StrEnum):
    USER = "user"
    AUTOPILOT = "autopilot"


class AnalyticsScope(StrEnum):
    CHANNEL = "channel"
    VIDEO = "video"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AUTOPILOT = "autopilot"


class ComponentStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    CONNECTED = "CONNECTED"
    NOT_CONNECTED = "NOT CONNECTED"
    NOT_CONFIGURED = "NOT CONFIGURED"
