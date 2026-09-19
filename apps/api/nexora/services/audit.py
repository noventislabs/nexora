"""Append-only audit trail for state transitions that matter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import AuditLog
from nexora.db.models.enums import ActorType


def record(
    session: Session,
    *,
    action: str,
    actor_type: ActorType | str = ActorType.USER,
    user_id: uuid.UUID | None = None,
    channel_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    summary: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_type=str(actor_type),
        user_id=user_id,
        channel_id=channel_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary,
        before=before,
        after=after,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:512] or None,
        created_at=datetime.now(UTC),
    )
    session.add(entry)
    return entry


def recent(
    session: Session,
    *,
    channel_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    if channel_id is not None:
        stmt = stmt.where(AuditLog.channel_id == channel_id)
    return list(session.execute(stmt).scalars())
