"""Durable automation locks.

The guarantee: two workers handed the same channel, or the same topic, at the same
moment must not both proceed. That guarantee is enforced by a **partial unique index**
in PostgreSQL, so it holds even if Redis drops a message, a worker is killed mid-run,
or the queue delivers a job twice — all of which happen.

An expired lock can be taken over, because a worker that dies mid-render must not block
its channel forever. A takeover is recorded on the old row rather than overwriting it,
so "why did this run twice" has an answer.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict
from nexora.core.logging import get_logger
from nexora.db.models import AutomationLock, Channel

logger = get_logger(__name__)

#: Long enough for a full pipeline traversal including a render, short enough that a
#: dead worker does not strand the channel for a day.
DEFAULT_TTL = timedelta(hours=2)

CHANNEL_RUN_KEY = "channel-run"


def holder_id() -> str:
    """Identifies the process holding a lock, for attributing a stuck one."""
    return f"{socket.gethostname()}:{os.getpid()}"


def topic_key(fingerprint: str) -> str:
    return f"topic:{fingerprint}"


def acquire(
    session: Session,
    channel: Channel,
    lock_key: str,
    *,
    ttl: timedelta = DEFAULT_TTL,
    automation_run_id: uuid.UUID | None = None,
    content_project_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AutomationLock:
    """Claim ``lock_key`` for this channel, or raise :class:`Conflict`.

    The insert is the lock. If a concurrent worker wins the race, the unique index
    rejects this insert and the loser is told who holds it — rather than both
    proceeding to render the same video.
    """
    now = now or datetime.now(UTC)

    existing = session.execute(
        select(AutomationLock).where(
            AutomationLock.channel_id == channel.id,
            AutomationLock.lock_key == lock_key,
            AutomationLock.released_at.is_(None),
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.expires_at > now:
            raise Conflict(
                f"'{lock_key}' is already held for this channel until "
                f"{existing.expires_at.isoformat()}.",
                details={
                    "lock_key": lock_key,
                    "holder": existing.holder,
                    "expires_at": existing.expires_at.isoformat(),
                },
            )
        # Expired: take it over, and say so on the old row rather than deleting it.
        existing.released_at = now
        existing.release_reason = (
            f"Expired at {existing.expires_at.isoformat()} while held by "
            f"{existing.holder or 'an unknown worker'}; taken over by {holder_id()}."
        )
        session.flush()
        logger.warning(
            "automation.lock_taken_over",
            extra={"channel_id": str(channel.id), "lock_key": lock_key},
        )

    lock = AutomationLock(
        channel_id=channel.id,
        lock_key=lock_key,
        automation_run_id=automation_run_id,
        content_project_id=content_project_id,
        holder=holder_id(),
        acquired_at=now,
        expires_at=now + ttl,
    )
    session.add(lock)
    try:
        session.flush()
    except IntegrityError as exc:
        # Another worker inserted between our check and our insert. The database is the
        # arbiter, and it said no.
        session.rollback()
        raise Conflict(
            f"'{lock_key}' was claimed by another worker for this channel.",
            details={"lock_key": lock_key},
        ) from exc
    return lock


def release(
    session: Session,
    lock: AutomationLock,
    *,
    reason: str = "completed",
    now: datetime | None = None,
) -> AutomationLock:
    """Release a lock, keeping the row as the record that the work happened."""
    if lock.released_at is None:
        lock.released_at = now or datetime.now(UTC)
        lock.release_reason = reason[:1000]
        session.flush()
    return lock


def release_key(
    session: Session, channel: Channel, lock_key: str, *, reason: str = "completed"
) -> bool:
    lock = session.execute(
        select(AutomationLock).where(
            AutomationLock.channel_id == channel.id,
            AutomationLock.lock_key == lock_key,
            AutomationLock.released_at.is_(None),
        )
    ).scalar_one_or_none()
    if lock is None:
        return False
    release(session, lock, reason=reason)
    return True


def live_locks(session: Session, channel_id: uuid.UUID) -> list[AutomationLock]:
    return list(
        session.execute(
            select(AutomationLock).where(
                AutomationLock.channel_id == channel_id,
                AutomationLock.released_at.is_(None),
            )
        ).scalars()
    )


def release_expired(session: Session, *, now: datetime | None = None) -> int:
    """Sweep locks whose holder never came back. Returns how many were released."""
    now = now or datetime.now(UTC)
    stale = list(
        session.execute(
            select(AutomationLock).where(
                AutomationLock.released_at.is_(None),
                AutomationLock.expires_at <= now,
            )
        ).scalars()
    )
    for lock in stale:
        lock.released_at = now
        lock.release_reason = (
            f"Expired at {lock.expires_at.isoformat()} without being released by "
            f"{lock.holder or 'an unknown worker'}."
        )
    if stale:
        session.flush()
        logger.warning("automation.locks_swept", extra={"count": len(stale)})
    return len(stale)
