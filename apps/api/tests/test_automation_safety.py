"""The stops, the limits and the locks.

These are the tests that decide whether it is safe to leave NEXORA running unattended.
Each one asserts that something does **not** happen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.core.errors import Conflict, PermissionDenied, SafetyBlocked
from nexora.db.models import (
    AutomationLock,
    Channel,
    ContentProject,
    PublishJob,
    User,
)
from nexora.db.models.enums import AutomationMode, RunStatus
from nexora.services import channels as channel_service
from nexora.services import killswitch
from nexora.services.automation import gates, locks

NOW = datetime.now(UTC)


@pytest.fixture
def automation(db: Session, channel: Channel):
    return channel_service.get_automation_settings(db, channel.id)


def enable_autonomous(db: Session, channel: Channel) -> None:
    """Turn every switch on. A channel in this state may publish unattended."""
    row = channel_service.get_automation_settings(db, channel.id)
    row.mode = AutomationMode.AUTONOMOUS.value
    row.automation_enabled = True
    row.publishing_enabled = True
    row.autopilot_enabled = True
    row.auto_publish_enabled = True
    row.require_human_approval = False
    db.flush()


def publish_job(
    db: Session,
    channel: Channel,
    project: ContentProject,
    *,
    status: str,
    verified: bool,
    key: str,
    created_at: datetime | None = None,
) -> PublishJob:
    job = PublishJob(
        channel_id=channel.id,
        content_project_id=project.id,
        status=status,
        privacy_status="private",
        authorized_by="autopilot",
        idempotency_key=key,
        verified_at=NOW if verified else None,
        created_at=created_at or NOW,
    )
    db.add(job)
    db.flush()
    return job


@pytest.fixture
def project(db: Session, channel: Channel) -> ContentProject:
    row = ContentProject(
        channel_id=channel.id,
        title="TEST FIXTURE project",
        status="ready",
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------- global kill switch
def test_the_global_stop_halts_every_channel_of_every_user(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["kids"])
    db.flush()
    for channel in (first, second):
        enable_autonomous(db, channel)

    killswitch.engage(db, reason="Upstream provider incident", user_id=user.id)
    db.commit()

    for channel in (first, second):
        automation = channel_service.get_automation_settings(db, channel.id)
        result = gates.can_automate(db, channel, automation)
        assert result.allowed is False
        assert result.blockers[0]["scope"] == "global"
        assert "Upstream provider incident" in result.blockers[0]["detail"]


def test_a_global_stop_overrides_every_channel_switch(db: Session, channel: Channel) -> None:
    """No combination of channel settings can defeat it."""
    enable_autonomous(db, channel)
    killswitch.engage(db, reason="Halt", user_id=None)
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    assert gates.can_publish_autonomously(db, channel, automation).allowed is False


def test_the_global_stop_is_reported_alone_not_buried_under_channel_settings(
    db: Session, channel: Channel
) -> None:
    """An operator must not be told to change a switch that cannot help."""
    killswitch.engage(db, reason="Halt", user_id=None)
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    result = gates.can_automate(db, channel, automation)
    assert len(result.blockers) == 1
    assert result.blockers[0]["scope"] == "global"


def test_a_stop_requires_a_reason(db: Session) -> None:
    from nexora.core.errors import ValidationError

    with pytest.raises(ValidationError, match="record why"):
        killswitch.engage(db, reason="   ", user_id=None)


def test_releasing_the_stop_re_enables_nothing(db: Session, channel: Channel) -> None:
    """Clearing a stop must never be a way to turn autopilot on."""
    automation = channel_service.get_automation_settings(db, channel.id)
    automation.automation_enabled = False
    automation.autopilot_enabled = False
    db.flush()

    killswitch.engage(db, reason="Halt", user_id=None)
    killswitch.release(db, user_id=None)
    db.commit()

    db.refresh(automation)
    assert automation.automation_enabled is False
    assert automation.autopilot_enabled is False
    assert killswitch.is_engaged(db) is False


def test_a_missing_switch_row_means_not_engaged(db: Session) -> None:
    """The safe default for a *stop* is off: absence never silently halts the system."""
    assert killswitch.is_engaged(db) is False


def test_require_clear_raises_while_engaged(db: Session) -> None:
    killswitch.engage(db, reason="Halt", user_id=None)
    db.commit()
    with pytest.raises(SafetyBlocked, match="global emergency stop"):
        killswitch.require_clear(db)


# ------------------------------------------------------------ channel-level stops
def test_a_channel_stop_halts_that_channel_only(db: Session, user: User) -> None:
    stopped = channel_service.create_channel(db, user, name="Stopped", categories=["technology"])
    running = channel_service.create_channel(db, user, name="Running", categories=["technology"])
    db.flush()
    for channel in (stopped, running):
        enable_autonomous(db, channel)

    stopped_automation = channel_service.get_automation_settings(db, stopped.id)
    stopped_automation.emergency_stop = True
    stopped_automation.emergency_stop_reason = "Bad render output"
    db.commit()

    assert gates.can_automate(db, stopped, stopped_automation).allowed is False
    running_automation = channel_service.get_automation_settings(db, running.id)
    assert gates.can_automate(db, running, running_automation).allowed is True


def test_automation_off_blocks_production(db: Session, channel: Channel, automation) -> None:
    enable_autonomous(db, channel)
    automation.automation_enabled = False
    db.flush()

    result = gates.can_automate(db, channel, automation)
    assert result.allowed is False
    assert any("Automation is OFF" in entry["detail"] for entry in result.blockers)


def test_publishing_off_blocks_upload_but_not_production(
    db: Session, channel: Channel, automation
) -> None:
    """The two switches are independent on purpose."""
    enable_autonomous(db, channel)
    automation.publishing_enabled = False
    db.flush()

    assert gates.can_automate(db, channel, automation).allowed is True
    result = gates.can_publish_autonomously(db, channel, automation)
    assert result.allowed is False
    assert any("Publishing is OFF" in entry["detail"] for entry in result.blockers)


# ------------------------------------------------------------------ autopilot levels
def test_only_autonomous_may_publish_without_a_person(
    db: Session, channel: Channel, automation
) -> None:
    enable_autonomous(db, channel)

    for mode, may_publish in (
        (AutomationMode.ASSISTED.value, False),
        (AutomationMode.SEMI_AUTONOMOUS.value, False),
        (AutomationMode.AUTONOMOUS.value, True),
    ):
        automation.mode = mode
        db.flush()
        assert gates.capabilities(automation)["publish"] is may_publish, mode
        result = gates.can_publish_autonomously(db, channel, automation)
        assert result.allowed is may_publish, mode


def test_only_semi_and_above_run_checks_unattended(automation) -> None:
    automation.mode = AutomationMode.ASSISTED.value
    assert gates.capabilities(automation)["run_checks"] is False
    automation.mode = AutomationMode.SEMI_AUTONOMOUS.value
    assert gates.capabilities(automation)["run_checks"] is True


def test_every_level_may_discover_research_write_and_produce(automation) -> None:
    for mode in (
        AutomationMode.ASSISTED.value,
        AutomationMode.SEMI_AUTONOMOUS.value,
        AutomationMode.AUTONOMOUS.value,
    ):
        automation.mode = mode
        capabilities = gates.capabilities(automation)
        assert all(capabilities[stage] for stage in ("discover", "research", "write", "produce"))


def test_human_approval_required_blocks_autonomous_publishing(
    db: Session, channel: Channel, automation
) -> None:
    enable_autonomous(db, channel)
    automation.require_human_approval = True
    db.flush()

    result = gates.can_publish_autonomously(db, channel, automation)
    assert result.allowed is False
    assert any("human approval" in entry["detail"] for entry in result.blockers)


# ------------------------------------------------------------------- daily limits
def test_only_a_verified_publish_counts_toward_the_daily_limit(
    db: Session, channel: Channel, automation, project
) -> None:
    """A failed upload has published nothing, and must not consume the day's budget."""
    enable_autonomous(db, channel)
    publish_job(db, channel, project, status=RunStatus.FAILED.value, verified=False, key="f1")
    publish_job(db, channel, project, status=RunStatus.FAILED.value, verified=False, key="f2")
    db.commit()

    counts = gates.daily_counts(db, channel, automation)
    assert counts.published == 0
    assert counts.failed == 2
    assert gates.can_publish_autonomously(db, channel, automation).allowed is True


def test_a_successful_but_unverified_job_does_not_count(
    db: Session, channel: Channel, automation, project
) -> None:
    """Until YouTube confirms the video on a read-back, nothing was published."""
    enable_autonomous(db, channel)
    publish_job(db, channel, project, status=RunStatus.SUCCESS.value, verified=False, key="u1")
    db.commit()

    assert gates.daily_counts(db, channel, automation).published == 0


def test_the_daily_limit_blocks_once_reached(
    db: Session, channel: Channel, automation, project
) -> None:
    enable_autonomous(db, channel)
    publish_job(db, channel, project, status=RunStatus.SUCCESS.value, verified=True, key="v1")
    db.commit()

    assert gates.daily_counts(db, channel, automation).published == 1
    result = gates.can_publish_autonomously(db, channel, automation)
    assert result.allowed is False
    assert any("already published" in entry["detail"] for entry in result.blockers)


def test_failures_are_reported_as_a_warning_not_a_block(
    db: Session, channel: Channel, automation, project
) -> None:
    enable_autonomous(db, channel)
    publish_job(db, channel, project, status=RunStatus.FAILED.value, verified=False, key="f1")
    db.commit()

    result = gates.can_publish_autonomously(db, channel, automation)
    assert result.allowed is True
    assert any("do not count toward" in entry["detail"] for entry in result.warnings)


def test_the_day_is_the_channels_day_not_the_servers(
    db: Session, channel: Channel, automation
) -> None:
    """A limit of one per day means one per the operator's day."""
    automation.timezone = "Asia/Dhaka"
    db.flush()
    start, end, day, zone = gates.channel_day_bounds(automation, now=NOW)
    assert zone == "Asia/Dhaka"
    assert (end - start) == timedelta(days=1)

    automation.timezone = "America/Los_Angeles"
    db.flush()
    other_start, _, _, _ = gates.channel_day_bounds(automation, now=NOW)
    assert other_start != start


def test_an_unrecognised_timezone_falls_back_to_utc_rather_than_failing(
    db: Session, automation
) -> None:
    automation.timezone = "Mars/Olympus"
    db.flush()
    _, _, _, zone = gates.channel_day_bounds(automation, now=NOW)
    assert zone == "UTC"


# -------------------------------------------------------------------------- locks
def test_a_second_worker_cannot_take_a_held_lock(db: Session, channel: Channel) -> None:
    locks.acquire(db, channel, "channel-run")
    db.flush()

    with pytest.raises(Conflict, match="already held"):
        locks.acquire(db, channel, "channel-run")


def test_the_database_constraint_is_the_guarantee(db: Session, channel: Channel) -> None:
    """Not a Redis key: a partial unique index, so a lost message cannot lose the lock."""
    locks.acquire(db, channel, "topic:abc")
    db.flush()

    duplicate = AutomationLock(
        channel_id=channel.id,
        lock_key="topic:abc",
        holder="a-second-worker",
        acquired_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    db.add(duplicate)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_two_channels_may_hold_the_same_key(db: Session, user: User) -> None:
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()

    locks.acquire(db, first, "channel-run")
    locks.acquire(db, second, "channel-run")
    db.commit()

    assert len(locks.live_locks(db, first.id)) == 1
    assert len(locks.live_locks(db, second.id)) == 1


def test_a_released_lock_can_be_reacquired(db: Session, channel: Channel) -> None:
    lock = locks.acquire(db, channel, "channel-run")
    locks.release(db, lock, reason="run finished")
    db.flush()

    again = locks.acquire(db, channel, "channel-run")
    assert again.id != lock.id
    # The old row survives as the record that the first run happened.
    assert lock.released_at is not None
    assert lock.release_reason == "run finished"


def test_an_expired_lock_can_be_taken_over_and_the_takeover_is_recorded(
    db: Session, channel: Channel
) -> None:
    """A worker that died mid-render must not block its channel forever."""
    stale = locks.acquire(db, channel, "channel-run", ttl=timedelta(seconds=-1))
    db.flush()

    fresh = locks.acquire(db, channel, "channel-run")
    db.commit()

    assert fresh.id != stale.id
    assert stale.released_at is not None
    assert "Expired" in stale.release_reason
    assert "taken over" in stale.release_reason


def test_sweeping_releases_only_expired_locks(db: Session, user: User) -> None:
    channel = channel_service.create_channel(db, user, name="Sweep", categories=["technology"])
    db.flush()
    live = locks.acquire(db, channel, "live-key")
    expired = locks.acquire(db, channel, "expired-key", ttl=timedelta(seconds=-1))
    db.flush()

    assert locks.release_expired(db) == 1
    db.commit()

    assert live.released_at is None
    assert expired.released_at is not None


# ----------------------------------------------------------------------- ownership
def test_a_job_whose_channel_belongs_to_another_user_is_refused(
    db: Session, user: User
) -> None:
    """Without this, a tampered channel_id would borrow another channel's OAuth token."""
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    theirs = channel_service.create_channel(db, other, name="Theirs", categories=["technology"])
    db.commit()

    with pytest.raises(PermissionDenied, match="does not belong"):
        gates.require_ownership(db, user.id, theirs)

    # The rightful owner passes.
    gates.require_ownership(db, other.id, theirs)


# -------------------------------------------------------------------------- API
def test_automation_endpoints_require_authentication(client: TestClient) -> None:
    import uuid

    for method, path in [
        ("get", "/api/automation/emergency-stop"),
        ("post", "/api/automation/emergency-stop"),
        ("get", "/api/automation/state"),
        ("post", "/api/automation/run"),
        ("get", "/api/automation/runs"),
        ("get", "/api/automation/locks"),
        ("post", "/api/automation/duplicate-check"),
        ("get", f"/api/automation/fact-gate/{uuid.uuid4()}"),
    ]:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"


def test_engaging_the_stop_cancels_queued_upload_jobs(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.queue import jobs as job_queue
    from nexora.queue.types import YOUTUBE_UPLOAD

    job = job_queue.enqueue(db, YOUTUBE_UPLOAD, channel_id=channel.id, payload={})
    db.commit()

    body = auth_client.post(
        "/api/automation/emergency-stop", json={"reason": "Provider incident"}
    ).json()

    assert body["engaged"] is True
    assert body["cancelled_jobs"] >= 1
    db.refresh(job)
    assert job.status == "CANCELLED"


def test_starting_a_run_is_refused_while_the_global_stop_is_engaged(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    enable_autonomous(db, channel)
    db.commit()
    auth_client.post("/api/automation/emergency-stop", json={"reason": "Halt"})

    response = auth_client.post(
        "/api/automation/run", json={"channel_id": str(channel.id), "background": True}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "safety_blocked"


def test_starting_a_run_is_refused_while_automation_is_off(
    auth_client: TestClient, channel: Channel
) -> None:
    """A manual trigger is not a way around the gates the scheduler goes through."""
    response = auth_client.post(
        "/api/automation/run", json={"channel_id": str(channel.id), "background": True}
    )
    assert response.status_code == 409
    assert "Automation is OFF" in response.json()["message"]


def test_the_state_endpoint_reports_every_switch_and_the_daily_counts(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/automation/state").json()

    assert body["switches"]["automation_enabled"] is False
    assert body["switches"]["auto_publish_enabled"] is False
    assert body["switches"]["require_human_approval"] is True
    assert body["daily"]["published_today"] == 0
    assert "Only a publish job verified on YouTube" in body["daily"]["counting_rule"]
    assert body["can_publish_autonomously"]["allowed"] is False
    assert "checked on the server" in body["note"]


def test_the_state_endpoint_describes_all_three_levels(
    auth_client: TestClient, channel: Channel
) -> None:
    levels = auth_client.get("/api/automation/state").json()["levels"]

    assert levels["assisted"]["publish"] is False
    assert levels["semi_autonomous"]["publish"] is False
    assert levels["semi_autonomous"]["run_checks"] is True
    assert levels["autonomous"]["publish"] is True


def test_another_users_automation_state_is_not_reachable(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    theirs = channel_service.create_channel(db, other, name="Theirs")
    db.commit()

    assert auth_client.get(f"/api/automation/state?channel_id={theirs.id}").status_code == 403
