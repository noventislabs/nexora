"""Durable automation locks.

A lock is a **database row with a unique constraint**, not a Redis key. Redis is the
queue's wake-up signal and can lose a message; losing a lock would let two workers
research, script, render and upload the same topic twice, and the second upload is not
recoverable once YouTube has it. PostgreSQL is where the lock has to live.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, utc_column, uuid_pk


class AutomationLock(Base):
    """One channel's claim on one piece of work.

    ``released_at`` rather than deleting the row: a released lock is the record that a
    run happened, and its ``lock_key`` is what stops the same topic being picked up
    again a minute later.
    """

    __tablename__ = "automation_locks"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: What is being claimed, e.g. ``topic:<fingerprint>`` or ``channel-run``.
    lock_key: Mapped[str] = mapped_column(String(128), nullable=False)
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("automation_runs.id", ondelete="SET NULL"), index=True
    )
    content_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_projects.id", ondelete="SET NULL"), index=True
    )
    #: The worker that holds it, so a stuck lock can be attributed.
    holder: Mapped[str | None] = mapped_column(String(128))
    acquired_at = utc_column(nullable=False)
    #: A lock is never held forever: a worker that dies mid-render must not block the
    #: channel permanently, so an expired lock can be taken over and the takeover is
    #: recorded rather than silently overwriting.
    expires_at = utc_column(nullable=False, index=True)
    released_at = utc_column(index=True)
    release_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Partial unique index: at most one *live* holder per (channel, key). Released
        # locks stay as history and do not block a later legitimate re-run.
        Index(
            "uq_automation_locks_live",
            "channel_id",
            "lock_key",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index("ix_automation_locks_channel_released", "channel_id", "released_at"),
    )
