"""initial schema

Creates every core table for NEXORA AI AUTOPILOT.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from nexora.db.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ordered so foreign keys resolve without deferral.
_TABLES = [
    "users",
    "auth_sessions",
    "channels",
    "channel_settings",
    "automation_settings",
    "youtube_connections",
    "oauth_states",
    "trend_sources",
    "trending_topics",
    "topic_candidates",
    "topic_research",
    "content_projects",
    "content_scripts",
    "script_versions",
    "fact_checks",
    "metadata_versions",
    "quality_checks",
    "copyright_checks",
    "video_assets",
    "voice_jobs",
    "video_projects",
    "video_render_jobs",
    "thumbnails",
    "publish_jobs",
    "youtube_videos",
    "analytics_snapshots",
    "performance_observations",
    "content_performance_features",
    "jobs",
    "job_logs",
    "automation_runs",
    "audit_logs",
    "system_settings",
]


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+; pgcrypto is only needed on older
    # servers, and CREATE EXTENSION IF NOT EXISTS is a no-op when it is already there.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in _TABLES],
        checkfirst=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in reversed(_TABLES)],
        checkfirst=False,
    )
