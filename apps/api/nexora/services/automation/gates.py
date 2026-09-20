"""Whether automation may act on a channel at all, and how far it may go.

This is the layer between "a scheduler woke up" and "the pipeline started". It answers
two questions, both server-side:

* May automation produce content for this channel right now?
* May it publish without a person, or must it stop and ask?

Precedence is strictest-first and never reversed: a global stop beats a channel's
settings, a channel stop beats its own automation switch, and nothing beats a stop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nexora.db.models import AutomationSettings, Channel, PublishJob
from nexora.db.models.enums import AutomationMode, RunStatus

#: The three levels the product defines. Each is a strict superset of the one before.
LEVEL_ASSISTED = AutomationMode.ASSISTED.value
LEVEL_SEMI = AutomationMode.SEMI_AUTONOMOUS.value
LEVEL_AUTONOMOUS = AutomationMode.AUTONOMOUS.value

#: Which stages each level may run without a person. Publishing is deliberately absent
#: from everything but AUTONOMOUS, and even there it is gated separately.
LEVEL_CAPABILITIES: dict[str, dict[str, bool]] = {
    LEVEL_ASSISTED: {
        "discover": True,
        "research": True,
        "write": True,
        "produce": True,
        "run_checks": False,
        "publish": False,
    },
    LEVEL_SEMI: {
        "discover": True,
        "research": True,
        "write": True,
        "produce": True,
        "run_checks": True,
        "publish": False,
    },
    LEVEL_AUTONOMOUS: {
        "discover": True,
        "research": True,
        "write": True,
        "produce": True,
        "run_checks": True,
        "publish": True,
    },
}


@dataclass
class GateResult:
    """Whether automation may proceed, and every reason it may not."""

    allowed: bool
    blockers: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def block(self, scope: str, detail: str) -> None:
        self.allowed = False
        self.blockers.append({"scope": scope, "detail": detail})

    def warn(self, scope: str, detail: str) -> None:
        self.warnings.append({"scope": scope, "detail": detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }


@dataclass
class DailyCounts:
    """Today's publishing activity, in the channel's own timezone.

    ``published`` counts **only verified successful uploads**. A failed attempt is
    counted separately and never consumes the day's budget — a channel that failed
    three times has published nothing, and must still be allowed its video.
    """

    published: int
    scheduled: int
    failed: int
    day: date
    timezone: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "published_today": self.published,
            "scheduled_today": self.scheduled,
            "failed_today": self.failed,
            "day": self.day.isoformat(),
            "timezone": self.timezone,
            "counting_rule": (
                "Only a publish job verified on YouTube counts as published. A failed "
                "or retrying upload does not consume the day's limit."
            ),
        }


def channel_day_bounds(
    automation: AutomationSettings, *, now: datetime | None = None
) -> tuple[datetime, datetime, date, str]:
    """Today, in the channel's timezone rather than the server's.

    A limit of "one per day" means one per the operator's day. Counting in UTC would
    roll the limit over at 6am for a channel in Asia/Dhaka.
    """
    now = now or datetime.now(UTC)
    try:
        zone = ZoneInfo(automation.timezone)
    except Exception:
        zone = UTC
    local = now.astimezone(zone)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        start_local.astimezone(UTC),
        (start_local + timedelta(days=1)).astimezone(UTC),
        local.date(),
        str(zone),
    )


def daily_counts(
    session: Session,
    channel: Channel,
    automation: AutomationSettings,
    *,
    now: datetime | None = None,
) -> DailyCounts:
    start, end, day, zone = channel_day_bounds(automation, now=now)

    def count(*conditions) -> int:
        return session.execute(
            select(func.count())
            .select_from(PublishJob)
            .where(PublishJob.channel_id == channel.id, *conditions)
        ).scalar_one()

    published = count(
        PublishJob.status == RunStatus.SUCCESS.value,
        # Verified means YouTube confirmed the video on a read-back.
        PublishJob.verified_at.is_not(None),
        PublishJob.verified_at >= start,
        PublishJob.verified_at < end,
    )
    scheduled = count(
        PublishJob.scheduled_for.is_not(None),
        PublishJob.scheduled_for >= start,
        PublishJob.scheduled_for < end,
        PublishJob.status.notin_([RunStatus.SUCCESS.value, RunStatus.CANCELLED.value]),
    )
    failed = count(
        PublishJob.status == RunStatus.FAILED.value,
        PublishJob.created_at >= start,
        PublishJob.created_at < end,
    )
    return DailyCounts(
        published=published, scheduled=scheduled, failed=failed, day=day, timezone=zone
    )


def capabilities(automation: AutomationSettings) -> dict[str, bool]:
    """What this channel's level permits, before any stop is applied."""
    return dict(LEVEL_CAPABILITIES.get(automation.mode, LEVEL_CAPABILITIES[LEVEL_ASSISTED]))


def can_automate(
    session: Session,
    channel: Channel,
    automation: AutomationSettings,
    *,
    now: datetime | None = None,
) -> GateResult:
    """May automation produce content for this channel right now?

    Deliberately does not consider publishing: a channel may be allowed to research,
    write and render while every upload still waits for a person.
    """
    from nexora.services import killswitch

    result = GateResult(allowed=True)

    global_state = killswitch.state(session)
    if global_state.engaged:
        result.block(
            "global",
            "The global emergency stop is engaged."
            + (f" Reason: {global_state.reason}" if global_state.reason else ""),
        )
        # A global stop is final. Reporting the channel's settings underneath it would
        # suggest they could change the outcome; they cannot.
        return result

    if automation.emergency_stop:
        result.block(
            "channel",
            "This channel's emergency stop is engaged."
            + (
                f" Reason: {automation.emergency_stop_reason}"
                if automation.emergency_stop_reason
                else ""
            ),
        )
        return result

    if not automation.automation_enabled:
        result.block(
            "channel",
            "Automation is OFF for this channel. It is off by default and is only ever "
            "turned on deliberately.",
        )
    if not automation.autopilot_enabled:
        result.block("channel", "Autopilot is OFF for this channel.")

    if not channel.is_active:
        result.block("channel", "This channel is not active.")

    return result


def can_publish_autonomously(
    session: Session,
    channel: Channel,
    automation: AutomationSettings,
    *,
    now: datetime | None = None,
) -> GateResult:
    """May automation upload without a person?

    Every layer must agree. Any one of them saying no is enough, and the answer names
    which — an operator needs to know which switch to change.
    """
    result = can_automate(session, channel, automation, now=now)
    if not result.allowed:
        return result

    if not automation.publishing_enabled:
        result.block("channel", "Publishing is OFF for this channel.")

    if not capabilities(automation)["publish"]:
        result.block(
            "channel",
            f"Automation mode is '{automation.mode}'. Only 'autonomous' may publish "
            "without a person.",
        )

    if not automation.auto_publish_enabled:
        result.block(
            "channel",
            "Auto-publishing is OFF for this channel. It is off by default.",
        )

    if automation.require_human_approval:
        result.block(
            "channel",
            "This channel requires human approval before publishing.",
        )

    counts = daily_counts(session, channel, automation, now=now)
    if counts.published >= automation.max_videos_per_day:
        result.block(
            "limit",
            f"This channel has already published {counts.published} video"
            f"{'' if counts.published == 1 else 's'} today "
            f"({counts.day}, {counts.timezone}), and its limit is "
            f"{automation.max_videos_per_day}.",
        )
    if counts.failed:
        # Informational, never a block: a failure has not consumed the day's budget.
        result.warn(
            "limit",
            f"{counts.failed} publish attempt{'' if counts.failed == 1 else 's'} failed "
            "today. Failures do not count toward the daily limit.",
        )

    return result


def automation_state(
    session: Session, channel: Channel, automation: AutomationSettings
) -> dict[str, Any]:
    """The whole automation picture for one channel, for the API and the UI."""
    from nexora.services import killswitch

    produce = can_automate(session, channel, automation)
    publish = can_publish_autonomously(session, channel, automation)
    counts = daily_counts(session, channel, automation)

    return {
        "channel_id": str(channel.id),
        "mode": automation.mode,
        "level_capabilities": capabilities(automation),
        "switches": {
            "automation_enabled": automation.automation_enabled,
            "publishing_enabled": automation.publishing_enabled,
            "autopilot_enabled": automation.autopilot_enabled,
            "auto_publish_enabled": automation.auto_publish_enabled,
            "require_human_approval": automation.require_human_approval,
            "emergency_stop": automation.emergency_stop,
        },
        "global_emergency_stop": killswitch.state(session).to_dict(),
        "can_produce": produce.to_dict(),
        "can_publish_autonomously": publish.to_dict(),
        "daily": counts.to_dict(),
        "limits": {
            "max_videos_per_day": automation.max_videos_per_day,
            "max_videos_per_week": automation.max_videos_per_week,
            "min_interval_minutes": automation.min_interval_minutes,
        },
        "note": (
            "Every switch here is checked on the server before any job runs. Disabling "
            "a control in the browser is not what stops automation."
        ),
    }


def require_ownership(session: Session, user_id: uuid.UUID, channel: Channel) -> None:
    """A job's channel must belong to the job's user.

    Automation jobs carry both ids, and this is where they are checked against each
    other. Without it, a job whose channel_id was tampered with would publish one
    user's content to another user's channel using that channel's OAuth token.
    """
    from nexora.core.errors import PermissionDenied

    if channel.user_id != user_id:
        raise PermissionDenied(
            "This automation job's channel does not belong to its user.",
        )
