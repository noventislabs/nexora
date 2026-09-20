"""phase 7 autonomous automation

Adds what the orchestrator needs to run a channel unattended without ever running it
twice or publishing something it should not.

* ``automation_settings`` gains ``automation_enabled`` and ``publishing_enabled`` —
  two master switches separate from the autopilot flags, so producing content and
  uploading it can be stopped independently. ``automation_enabled`` defaults to FALSE
  because automation is off for a new channel; ``publishing_enabled`` defaults to TRUE
  because it gates *manual* publishing too, and an existing channel that could publish
  yesterday must still be able to today. ``allow_unverified_commentary`` is the single
  documented exception to the fact-check gate and defaults to FALSE.
* ``content_projects`` gains ``topic_fingerprint`` for deterministic deduplication and
  ``editorial_format`` to distinguish an explainer (held to the fact-check gate without
  exception) from labelled commentary.
* ``automation_locks`` is new. Its partial unique index is the actual concurrency
  guarantee: at most one live holder per (channel, lock_key). Redis can lose a message;
  a duplicate YouTube upload cannot be taken back.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automation_settings",
        sa.Column(
            "automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "publishing_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "automation_settings",
        sa.Column(
            "allow_unverified_commentary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "content_projects",
        sa.Column(
            "editorial_format", sa.String(length=24), nullable=False, server_default="explainer"
        ),
    )
    op.add_column(
        "content_projects", sa.Column("topic_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_content_projects_topic_fingerprint"), "content_projects", ["topic_fingerprint"]
    )

    op.create_table(
        "automation_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lock_key", sa.String(length=128), nullable=False),
        sa.Column("automation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("holder", sa.String(length=128), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["automation_run_id"], ["automation_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["content_project_id"], ["content_projects.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_automation_locks_channel_id"), "automation_locks", ["channel_id"])
    op.create_index(
        op.f("ix_automation_locks_automation_run_id"), "automation_locks", ["automation_run_id"]
    )
    op.create_index(
        op.f("ix_automation_locks_content_project_id"), "automation_locks", ["content_project_id"]
    )
    op.create_index(op.f("ix_automation_locks_expires_at"), "automation_locks", ["expires_at"])
    op.create_index(op.f("ix_automation_locks_released_at"), "automation_locks", ["released_at"])
    op.create_index(
        "ix_automation_locks_channel_released", "automation_locks", ["channel_id", "released_at"]
    )
    # The concurrency guarantee. Two workers racing for the same topic: one insert
    # succeeds, the other gets a unique violation and stands down.
    op.create_index(
        "uq_automation_locks_live",
        "automation_locks",
        ["channel_id", "lock_key"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("automation_locks")
    op.drop_index(op.f("ix_content_projects_topic_fingerprint"), table_name="content_projects")
    op.drop_column("content_projects", "topic_fingerprint")
    op.drop_column("content_projects", "editorial_format")
    op.drop_column("automation_settings", "allow_unverified_commentary")
    op.drop_column("automation_settings", "publishing_enabled")
    op.drop_column("automation_settings", "automation_enabled")
