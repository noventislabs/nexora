"""SQLAlchemy models. Importing this package registers every table on ``Base.metadata``."""

from nexora.db.base import Base
from nexora.db.models.content import (
    ContentProject,
    ContentScript,
    CopyrightCheck,
    FactCheck,
    MetadataVersion,
    QualityCheck,
    ScriptVersion,
)
from nexora.db.models.identity import (
    AuthSession,
    AutomationSettings,
    Channel,
    ChannelSettings,
    OAuthState,
    User,
    YouTubeConnection,
)
from nexora.db.models.media import Thumbnail, VideoAsset, VideoProject, VideoRenderJob, VoiceJob
from nexora.db.models.publishing import (
    AnalyticsSnapshot,
    ContentPerformanceFeature,
    PerformanceObservation,
    PublishJob,
    YouTubeVideo,
)
from nexora.db.models.system import AuditLog, AutomationRun, Job, JobLog, SystemSetting
from nexora.db.models.trends import TopicCandidate, TopicResearch, TrendingTopic, TrendSource

__all__ = [
    "AnalyticsSnapshot",
    "AuditLog",
    "AuthSession",
    "AutomationRun",
    "AutomationSettings",
    "Base",
    "Channel",
    "ChannelSettings",
    "ContentPerformanceFeature",
    "ContentProject",
    "ContentScript",
    "CopyrightCheck",
    "FactCheck",
    "Job",
    "JobLog",
    "MetadataVersion",
    "OAuthState",
    "PerformanceObservation",
    "PublishJob",
    "QualityCheck",
    "ScriptVersion",
    "SystemSetting",
    "Thumbnail",
    "TopicCandidate",
    "TopicResearch",
    "TrendSource",
    "TrendingTopic",
    "User",
    "VideoAsset",
    "VideoProject",
    "VideoRenderJob",
    "VoiceJob",
    "YouTubeConnection",
    "YouTubeVideo",
]
