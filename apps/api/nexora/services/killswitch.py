"""Global emergency stop.

A single row in ``system_settings`` that every automated path consults **server-side**
before doing anything irreversible. Disabling a button in the browser is not a kill
switch: the queue worker never renders that button, and an attacker or a stale tab
never sees it. This is the check that actually holds.

Precedence, strictest first:

1. Global emergency stop — halts every channel of every user.
2. Channel emergency stop — halts one channel.
3. Channel automation / publishing switches — halt one kind of work.

A stop never silently un-stops. Clearing it is an explicit act with its own audit
entry, and clearing it does not re-enable anything that was off beforehand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nexora.core.logging import get_logger
from nexora.db.models import SystemSetting
from nexora.services import audit

logger = get_logger(__name__)

KEY = "automation.emergency_stop"


@dataclass(frozen=True)
class KillSwitchState:
    engaged: bool
    reason: str | None
    engaged_at: datetime | None
    engaged_by: uuid.UUID | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "engaged": self.engaged,
            "reason": self.reason,
            "engaged_at": self.engaged_at.isoformat() if self.engaged_at else None,
            "engaged_by": str(self.engaged_by) if self.engaged_by else None,
            "scope": "global",
            "note": (
                "While engaged, no channel of any user produces or publishes content. "
                "This is checked on the server before every job runs, not only in the UI."
            ),
        }


def _row(session: Session) -> SystemSetting | None:
    return session.get(SystemSetting, KEY)


def state(session: Session) -> KillSwitchState:
    """Read the switch. A missing row means "not engaged", which is the safe default
    for a *stop* — the system runs, and nothing is silently halted by absence."""
    row = _row(session)
    if row is None or not row.value:
        return KillSwitchState(engaged=False, reason=None, engaged_at=None, engaged_by=None)

    value = row.value or {}
    engaged_at = value.get("engaged_at")
    engaged_by = value.get("engaged_by")
    return KillSwitchState(
        engaged=bool(value.get("engaged")),
        reason=value.get("reason"),
        engaged_at=datetime.fromisoformat(engaged_at) if engaged_at else None,
        engaged_by=uuid.UUID(engaged_by) if engaged_by else None,
    )


def is_engaged(session: Session) -> bool:
    return state(session).engaged


def engage(
    session: Session, *, reason: str, user_id: uuid.UUID | None = None
) -> KillSwitchState:
    """Halt all automation everywhere.

    A reason is required. An operator reading the audit log a week later needs to know
    why the system stopped, and "someone pressed the button" is not an answer.
    """
    reason = (reason or "").strip()
    if not reason:
        from nexora.core.errors import ValidationError

        raise ValidationError("An emergency stop must record why it was engaged.")

    now = datetime.now(UTC)
    row = _row(session)
    before = state(session).to_dict()
    value = {
        "engaged": True,
        "reason": reason[:1000],
        "engaged_at": now.isoformat(),
        "engaged_by": str(user_id) if user_id else None,
    }
    if row is None:
        row = SystemSetting(
            key=KEY,
            value=value,
            description="Global automation emergency stop.",
            updated_by=user_id,
        )
        session.add(row)
    else:
        row.value = value
        row.updated_by = user_id
    session.flush()

    logger.warning("automation.kill_switch_engaged", extra={"reason": reason[:200]})
    audit.record(
        session,
        action="automation.emergency_stop_engaged",
        user_id=user_id,
        entity_type="system_setting",
        before=before,
        after=state(session).to_dict(),
    )
    return state(session)


def release(session: Session, *, user_id: uuid.UUID | None = None) -> KillSwitchState:
    """Clear the global stop.

    This re-enables *nothing* on its own: every channel keeps whatever automation and
    publishing settings it had. Releasing a stop must not be a way to turn autopilot on.
    """
    row = _row(session)
    before = state(session).to_dict()
    if row is not None:
        row.value = {"engaged": False, "released_at": datetime.now(UTC).isoformat()}
        row.updated_by = user_id
        session.flush()

    logger.warning("automation.kill_switch_released")
    audit.record(
        session,
        action="automation.emergency_stop_released",
        user_id=user_id,
        entity_type="system_setting",
        before=before,
        after=state(session).to_dict(),
    )
    return state(session)


def require_clear(session: Session) -> None:
    """Raise unless automation may proceed. Called before every automated action."""
    from nexora.core.errors import SafetyBlocked

    current = state(session)
    if current.engaged:
        raise SafetyBlocked(
            "The global emergency stop is engaged. No content is produced or published "
            "while it is on."
            + (f" Reason: {current.reason}" if current.reason else ""),
            details={"scope": "global", "reason": current.reason},
        )
