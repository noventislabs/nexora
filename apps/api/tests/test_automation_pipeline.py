"""Topic deduplication, the fact-check publish gate, and the orchestrator run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nexora.db.models import (
    AutomationRun,
    Channel,
    ContentProject,
    FactCheck,
    TopicCandidate,
    User,
    YouTubeVideo,
)
from nexora.db.models.enums import AutomationMode, CheckStatus, RunStatus
from nexora.services import channels as channel_service
from nexora.services import killswitch
from nexora.services.automation import dedupe, locks, orchestrator
from nexora.services.automation.factgate import evaluate as evaluate_fact_gate

NOW = datetime.now(UTC)


def make_project(db: Session, channel: Channel, title: str, *, status: str = "ready"):
    row = ContentProject(
        channel_id=channel.id,
        title=title,
        status=status,
        video_format="long_form",
        target_duration_seconds=480,
        language="en",
        topic_fingerprint=dedupe.fingerprint(title),
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def make_check(
    db: Session, project: ContentProject, *, status: str, claims: list[dict]
) -> FactCheck:
    check = FactCheck(
        content_project_id=project.id,
        script_version_id=None,
        status=status,
        supported_count=sum(1 for c in claims if c["verdict"] == "SUPPORTED"),
        claims=claims,
        created_at=NOW,
    )
    db.add(check)
    db.flush()
    return check


def claim(assertion: str, verdict: str) -> dict:
    return {"assertion": assertion, "verdict": verdict, "reasons": ["TEST FIXTURE reason"]}


# ------------------------------------------------------------------ fingerprints
def test_the_same_topic_always_produces_the_same_fingerprint() -> None:
    """Deterministic: recomputable by hand from the stored tokens."""
    first = dedupe.fingerprint("Semiconductor supply chains under pressure")
    second = dedupe.fingerprint("Semiconductor supply chains under pressure")
    assert first == second and first is not None


def test_word_order_does_not_change_the_fingerprint() -> None:
    assert dedupe.fingerprint("chips and supply chains") == dedupe.fingerprint(
        "supply chains and chips"
    )


def test_a_topic_with_too_few_distinctive_words_has_no_fingerprint() -> None:
    """A two-word fingerprint would collide with everything and drop real videos."""
    assert dedupe.fingerprint("the a of") is None
    assert dedupe.fingerprint("New chips") is None


def test_similarity_is_a_real_ratio_not_a_random_number() -> None:
    assert dedupe.similarity(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert dedupe.similarity(["a", "b"], ["c", "d"]) == 0.0
    assert dedupe.similarity(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert dedupe.similarity([], ["a"]) == 0.0


# ------------------------------------------------------------------ deduplication
def test_an_identical_topic_is_a_duplicate(db: Session, channel: Channel) -> None:
    make_project(db, channel, "Why semiconductor supply chains keep failing")
    db.commit()

    result = dedupe.check(
        db, channel, title="Why semiconductor supply chains keep failing"
    )
    assert result.is_duplicate is True
    assert result.duplicates[0].similarity == 1.0
    assert result.duplicates[0].kind == "content_project"


def test_a_restatement_of_the_same_story_is_a_duplicate(
    db: Session, channel: Channel
) -> None:
    make_project(db, channel, "Semiconductor supply chains keep failing worldwide")
    db.commit()

    result = dedupe.check(
        db, channel, title="Worldwide semiconductor supply chains keep failing"
    )
    assert result.is_duplicate is True
    assert "Overlaps" in (result.reason or "")


def test_a_different_story_sharing_a_subject_is_not_a_duplicate(
    db: Session, channel: Channel
) -> None:
    make_project(db, channel, "Why semiconductor supply chains keep failing")
    db.commit()

    result = dedupe.check(
        db, channel, title="Quantum error correction reaches a practical milestone"
    )
    assert result.is_duplicate is False


def test_an_already_published_video_counts_as_covered_ground(
    db: Session, channel: Channel
) -> None:
    db.add(
        YouTubeVideo(
            channel_id=channel.id,
            youtube_video_id="fixtureVideo1",
            title="Why semiconductor supply chains keep failing",
            published_at=NOW - timedelta(days=5),
        )
    )
    db.commit()

    result = dedupe.check(
        db, channel, title="Why semiconductor supply chains keep failing"
    )
    assert result.is_duplicate is True
    assert result.duplicates[0].kind == "published_video"


def test_an_old_published_video_no_longer_blocks(db: Session, channel: Channel) -> None:
    db.add(
        YouTubeVideo(
            channel_id=channel.id,
            youtube_video_id="fixtureOld",
            title="Why semiconductor supply chains keep failing",
            published_at=NOW - dedupe.PUBLISHED_LOOKBACK - timedelta(days=1),
        )
    )
    db.commit()

    result = dedupe.check(
        db, channel, title="Why semiconductor supply chains keep failing"
    )
    assert result.is_duplicate is False


def test_a_rejected_topic_is_not_re_proposed(db: Session, channel: Channel) -> None:
    db.add(
        TopicCandidate(
            channel_id=channel.id,
            title="Why semiconductor supply chains keep failing",
            angle="An angle.",
            status="rejected",
            sources=[],
            created_at=NOW,
        )
    )
    db.commit()

    result = dedupe.check(
        db, channel, title="Why semiconductor supply chains keep failing", angle="An angle."
    )
    assert result.is_duplicate is True
    assert result.duplicates[0].kind == "rejected_topic"
    assert "already rejected" in result.duplicates[0].detail


def test_deduplication_never_crosses_a_channel_boundary(db: Session, user: User) -> None:
    """Two channels covering the same story is normal. One doing it twice is not."""
    first = channel_service.create_channel(db, user, name="First", categories=["technology"])
    second = channel_service.create_channel(db, user, name="Second", categories=["technology"])
    db.flush()
    make_project(db, first, "Why semiconductor supply chains keep failing")
    db.commit()

    assert dedupe.check(
        db, second, title="Why semiconductor supply chains keep failing"
    ).is_duplicate is False


def test_a_partial_overlap_is_reported_as_related_not_blocked(
    db: Session, channel: Channel
) -> None:
    make_project(db, channel, "Semiconductor supply chains under sustained pressure now")
    db.commit()

    result = dedupe.check(
        db, channel, title="Semiconductor manufacturing capacity expands sharply overseas"
    )
    assert result.is_duplicate is False
    assert result.to_dict()["method"].startswith("Jaccard")


def test_the_check_reports_the_tokens_it_compared(db: Session, channel: Channel) -> None:
    """The verdict is auditable: an operator can recompute it."""
    result = dedupe.check(db, channel, title="Semiconductor supply chains keep failing")
    assert "semiconductor" in result.tokens
    assert result.to_dict()["significant_tokens"] == result.tokens


# ------------------------------------------------------------------- fact gate
def test_a_clean_check_permits_automated_publishing(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "A well-sourced explainer about chips")
    make_check(
        db,
        project,
        status=CheckStatus.PASS.value,
        claims=[claim("Chips are made in fabs.", "SUPPORTED")],
    )
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is True


def test_an_unverified_claim_blocks_automated_publishing(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "An explainer with an unsupported claim inside")
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("A competitor will exit the market.", "UNVERIFIED")],
    )
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    result = evaluate_fact_gate(db, project, automation)

    assert result.allowed is False
    assert "unsupported by any source" in result.detail
    assert result.blocking_claims[0]["verdict"] == "UNVERIFIED"
    assert "turn unverified claims into factual statements" in result.detail


def test_a_contradicted_claim_blocks_automated_publishing(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "An explainer resolving a contested number")
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("The figure was exactly forty thousand.", "CONTRADICTED")],
    )
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    assert evaluate_fact_gate(db, project, automation).allowed is False


def test_insufficient_sources_blocks_automated_publishing(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "An explainer with nothing to check against")
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("Something happened.", "INSUFFICIENT_SOURCES")],
    )
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is False
    assert "not checkable for lack of sources" in result.detail


def test_no_fact_check_at_all_blocks_automated_publishing(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "A project nobody fact checked at all here")
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is False
    assert result.status == "NOT_RUN"
    assert "does not publish unchecked claims" in result.detail


def test_the_commentary_exemption_is_off_by_default(db: Session, channel: Channel) -> None:
    project = make_project(db, channel, "A commentary piece about the chip industry")
    project.editorial_format = "commentary"
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("An unverified opinion premise.", "UNVERIFIED")],
    )
    db.commit()

    automation = channel_service.get_automation_settings(db, channel.id)
    assert automation.allow_unverified_commentary is False
    assert evaluate_fact_gate(db, project, automation).allowed is False


def test_the_commentary_exemption_applies_only_to_commentary(
    db: Session, channel: Channel
) -> None:
    """An explainer asserts its claims as fact, so the exemption never reaches it."""
    project = make_project(db, channel, "An explainer with an unsupported claim inside")
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("An unverified premise.", "UNVERIFIED")],
    )
    automation = channel_service.get_automation_settings(db, channel.id)
    automation.allow_unverified_commentary = True
    db.commit()

    assert project.editorial_format == "explainer"
    assert evaluate_fact_gate(db, project, automation).allowed is False


def test_commentary_may_publish_unverified_premises_when_the_channel_permits_it(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "A commentary piece about the chip industry")
    project.editorial_format = "commentary"
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("An unverified premise.", "UNVERIFIED")],
    )
    automation = channel_service.get_automation_settings(db, channel.id)
    automation.allow_unverified_commentary = True
    db.commit()

    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is True
    assert result.exemption == "commentary_format"
    assert "labelled opinion rather than as verified fact" in result.detail


def test_commentary_never_exempts_a_contradicted_claim(
    db: Session, channel: Channel
) -> None:
    """Calling it opinion must not be a way around sources that actively disagree."""
    project = make_project(db, channel, "A commentary piece about a contested number")
    project.editorial_format = "commentary"
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("The figure was exactly forty thousand.", "CONTRADICTED")],
    )
    automation = channel_service.get_automation_settings(db, channel.id)
    automation.allow_unverified_commentary = True
    db.commit()

    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is False
    assert result.exemption is None


def test_overstatement_alone_blocks_only_when_the_channel_requires_a_clean_pass(
    db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "An explainer that overstates its own research")
    make_check(
        db,
        project,
        status=CheckStatus.REVIEW.value,
        claims=[claim("Stated more strongly than the research.", "PARTIALLY_SUPPORTED")],
    )
    automation = channel_service.get_automation_settings(db, channel.id)
    db.commit()

    assert automation.require_fact_check_pass is True
    assert evaluate_fact_gate(db, project, automation).allowed is False

    automation.require_fact_check_pass = False
    db.commit()
    result = evaluate_fact_gate(db, project, automation)
    assert result.allowed is True
    assert "No claim is unsupported" in result.detail


# ----------------------------------------------------------------- orchestrator
def enable_autonomous(db: Session, channel: Channel) -> None:
    row = channel_service.get_automation_settings(db, channel.id)
    row.mode = AutomationMode.AUTONOMOUS.value
    row.automation_enabled = True
    row.publishing_enabled = True
    row.autopilot_enabled = True
    row.auto_publish_enabled = True
    row.require_human_approval = False
    db.flush()


def test_a_run_with_no_relevant_trends_stops_cleanly_and_says_why(
    db: Session, channel: Channel
) -> None:
    """"Nothing was relevant today" is the system working, not an error."""
    enable_autonomous(db, channel)
    run = orchestrator.start_run(db, channel, trigger="manual")
    orchestrator.execute(db, channel, run)
    db.commit()

    assert run.status == RunStatus.SUCCESS.value
    assert run.error is None
    assert "No collected trend is relevant enough" in (run.stopped_reason or "")
    assert run.stages[0]["stage"] == "select_topic"
    assert run.stages[0]["status"] == "STOPPED"


def test_a_run_releases_its_channel_lock_when_it_finishes(
    db: Session, channel: Channel
) -> None:
    enable_autonomous(db, channel)
    run = orchestrator.start_run(db, channel, trigger="manual")
    orchestrator.execute(db, channel, run)
    db.commit()

    assert locks.live_locks(db, channel.id) == []


def test_a_second_run_stands_down_while_one_holds_the_channel(
    db: Session, channel: Channel
) -> None:
    enable_autonomous(db, channel)
    locks.acquire(db, channel, locks.CHANNEL_RUN_KEY)
    db.flush()

    run = orchestrator.start_run(db, channel, trigger="schedule")
    orchestrator.execute(db, channel, run)
    db.commit()

    assert run.status == RunStatus.CANCELLED.value
    assert "already held" in (run.stopped_reason or "")


def test_a_run_stops_when_the_global_switch_is_engaged_mid_flight(
    db: Session, channel: Channel
) -> None:
    """The stops are re-checked at every stage, not once at the start."""
    enable_autonomous(db, channel)
    killswitch.engage(db, reason="Halt everything", user_id=None)
    db.flush()

    run = orchestrator.start_run(db, channel, trigger="manual")
    orchestrator.execute(db, channel, run)
    db.commit()

    assert run.status == RunStatus.CANCELLED.value
    assert "emergency stop" in (run.stopped_reason or "").lower()
    assert locks.live_locks(db, channel.id) == []


def test_the_run_records_every_stage_it_attempted(db: Session, channel: Channel) -> None:
    enable_autonomous(db, channel)
    run = orchestrator.start_run(db, channel, trigger="manual")
    orchestrator.execute(db, channel, run)
    db.commit()

    payload = orchestrator.run_to_dict(run)
    assert payload["pipeline"] == list(orchestrator.STAGES)
    assert payload["stages"]
    assert all({"stage", "status", "detail", "at"} <= set(entry) for entry in payload["stages"])


def test_a_run_for_another_users_channel_is_refused(db: Session, user: User) -> None:
    from nexora.services import auth as auth_service
    from tests.conftest import TEST_PASSWORD

    other = auth_service.register_user(
        db, email="other@nexora.test", password=TEST_PASSWORD, display_name="Other"
    )
    theirs = channel_service.create_channel(db, other, name="Theirs", categories=["technology"])
    db.flush()
    theirs.user_id = user.id  # tamper: the row now claims a different owner
    db.flush()
    theirs.user_id = other.id
    db.flush()

    from nexora.core.errors import PermissionDenied
    from nexora.services.automation import gates

    with pytest.raises(PermissionDenied):
        gates.require_ownership(db, user.id, theirs)


# -------------------------------------------------------------------------- API
def test_the_duplicate_check_endpoint_explains_itself(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    make_project(db, channel, "Why semiconductor supply chains keep failing")
    db.commit()

    body = auth_client.post(
        "/api/automation/duplicate-check",
        json={
            "channel_id": str(channel.id),
            "title": "Why semiconductor supply chains keep failing",
        },
    ).json()

    assert body["is_duplicate"] is True
    assert body["duplicates"][0]["similarity"] == 1.0
    assert "Deterministic" in body["method"]


def test_the_fact_gate_endpoint_names_the_blocking_claims(
    auth_client: TestClient, db: Session, channel: Channel
) -> None:
    project = make_project(db, channel, "An explainer with an unsupported claim inside")
    make_check(
        db,
        project,
        status=CheckStatus.FAIL.value,
        claims=[claim("A competitor will exit the market.", "UNVERIFIED")],
    )
    db.commit()

    body = auth_client.get(f"/api/automation/fact-gate/{project.id}").json()

    assert body["allowed"] is False
    assert body["editorial_format"] == "explainer"
    assert body["blocking_claims"][0]["assertion"] == "A competitor will exit the market."
    assert "A person may still publish manually" in body["rule"]


def test_the_runs_endpoint_lists_this_channels_runs_only(
    auth_client: TestClient, db: Session, channel: Channel, user: User
) -> None:
    other_channel = channel_service.create_channel(db, user, name="Other", categories=["kids"])
    db.flush()
    db.add(
        AutomationRun(
            channel_id=other_channel.id,
            mode="assisted",
            trigger="manual",
            status=RunStatus.SUCCESS.value,
            stages=[],
            created_at=NOW,
        )
    )
    orchestrator.start_run(db, channel, trigger="manual")
    db.commit()

    body = auth_client.get(f"/api/automation/runs?channel_id={channel.id}").json()
    assert body["total"] == 1
    assert body["channel_id"] == str(channel.id)


def test_the_locks_endpoint_says_locks_are_database_rows(
    auth_client: TestClient, channel: Channel
) -> None:
    body = auth_client.get("/api/automation/locks").json()
    assert "not Redis keys" in body["note"]
