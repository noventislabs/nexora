"""The autopilot run: one traversal of the pipeline for one channel.

Design rules:

* **Resumable, not restartable.** Each stage records what it did on the
  ``automation_runs`` row. A run that dies during rendering resumes at rendering; it
  does not research and write the video again. Re-running a completed stage would
  also cost another LLM call and another minute of FFmpeg for no gain.
* **Every stage may stop the run.** A stage that cannot proceed sets a
  ``stopped_reason`` and the run ends cleanly as STOPPED. That is a normal outcome,
  not an error — "no topic was relevant enough today" is the system working.
* **The gates are re-checked at every stage**, not once at the start. A run may take
  an hour; an operator who engages the emergency stop during it expects the render in
  progress to be the last thing that happens.
* **Nothing is skipped to reach the end.** A missing provider stops the run where it
  is. There is no path through this module that produces a video without narration,
  or publishes without a fact check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.core.errors import (
    Conflict,
    NexoraError,
    ProviderNotConfigured,
    SafetyBlocked,
)
from nexora.core.logging import get_logger
from nexora.db.models import (
    AutomationRun,
    Channel,
    ChannelTopicRelevance,
    ContentProject,
    TopicCandidate,
    TrendingTopic,
)
from nexora.db.models.enums import (
    ActorType,
    CandidateStatus,
    PublishAuthorization,
    RelevanceStatus,
    RunStatus,
)
from nexora.services import audit
from nexora.services.automation import dedupe, gates, locks
from nexora.services.automation.factgate import evaluate as evaluate_fact_gate

logger = get_logger(__name__)

#: The pipeline, in order. A run walks this list and stops at the first stage that
#: cannot complete.
STAGES = (
    "select_topic",
    "research",
    "script",
    "fact_check",
    "voice",
    "render",
    "thumbnail",
    "metadata",
    "quality_check",
    "publish",
)

#: A topic must be at least this relevant to the channel before automation will build
#: a video from it. An operator can still pick anything by hand.
MIN_AUTOMATION_SCORE = 40


@dataclass
class StageOutcome:
    """What one stage did, and whether the run continues."""

    stage: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def should_continue(self) -> bool:
        return self.status == "COMPLETED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "data": self.data,
            "at": datetime.now(UTC).isoformat(),
        }


def completed(stage: str, detail: str, **data: Any) -> StageOutcome:
    return StageOutcome(stage=stage, status="COMPLETED", detail=detail, data=data)


def stopped(stage: str, detail: str, **data: Any) -> StageOutcome:
    """A deliberate halt. The run ends cleanly and says why."""
    return StageOutcome(stage=stage, status="STOPPED", detail=detail, data=data)


def skipped(stage: str, detail: str, **data: Any) -> StageOutcome:
    """Already done by a previous attempt at this run. Not repeated."""
    return StageOutcome(stage=stage, status="SKIPPED", detail=detail, data=data)


class RunStopped(Exception):
    """Raised internally to end a run cleanly at the current stage."""

    def __init__(self, outcome: StageOutcome):
        self.outcome = outcome
        super().__init__(outcome.detail)


# ---------------------------------------------------------------------- the run
def start_run(
    session: Session,
    channel: Channel,
    *,
    trigger: str = "manual",
    user_id: uuid.UUID | None = None,
) -> AutomationRun:
    """Create the run row. Does not execute anything."""
    automation = _automation(session, channel)
    run = AutomationRun(
        channel_id=channel.id,
        mode=automation.mode,
        trigger=trigger,
        triggered_by=user_id,
        status=RunStatus.QUEUED.value,
        stages=[],
        created_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def execute(
    session: Session,
    channel: Channel,
    run: AutomationRun,
    *,
    user_id: uuid.UUID | None = None,
) -> AutomationRun:
    """Walk the pipeline for one channel.

    Holds a channel-level lock for the whole traversal, so two schedulers firing at
    once produce one video rather than two.
    """
    gates.require_ownership(session, channel.user_id, channel)

    try:
        lock = locks.acquire(
            session, channel, locks.CHANNEL_RUN_KEY, automation_run_id=run.id
        )
    except Conflict as exc:
        _finish(session, run, RunStatus.CANCELLED.value, stopped_reason=str(exc))
        return run

    run.status = RunStatus.RUNNING.value
    run.started_at = datetime.now(UTC)
    session.flush()

    try:
        for stage in STAGES:
            _guard(session, channel, stage)
            run.current_stage = stage
            session.flush()

            outcome = _run_stage(session, channel, run, stage, user_id=user_id)
            _record(session, run, outcome)

            if not outcome.should_continue and outcome.status != "SKIPPED":
                _finish(
                    session,
                    run,
                    RunStatus.SUCCESS.value,
                    stopped_reason=f"{stage}: {outcome.detail}",
                )
                return run

        _finish(session, run, RunStatus.SUCCESS.value)
        return run

    except RunStopped as stop:
        _record(session, run, stop.outcome)
        _finish(
            session,
            run,
            RunStatus.SUCCESS.value,
            stopped_reason=f"{stop.outcome.stage}: {stop.outcome.detail}",
        )
        return run
    except SafetyBlocked as exc:
        _record(session, run, stopped(run.current_stage or "guard", str(exc)))
        _finish(session, run, RunStatus.CANCELLED.value, stopped_reason=str(exc))
        return run
    except ProviderNotConfigured as exc:
        # Not a failure: the deployment is missing a credential, which is a setup gap
        # rather than something that went wrong. The run stops cleanly and names what
        # is missing, so an operator sees a to-do rather than an error to debug.
        _record(
            session,
            run,
            stopped(run.current_stage or "unknown", str(exc), code=exc.code),
        )
        _finish(
            session,
            run,
            RunStatus.SUCCESS.value,
            stopped_reason=f"{run.current_stage}: {exc}",
        )
        return run
    except NexoraError as exc:
        logger.warning(
            "automation.run_failed",
            extra={"run_id": str(run.id), "stage": run.current_stage, "code": exc.code},
        )
        # Record the stage that failed. A run whose stage list simply stops is far
        # harder to diagnose than one that says which step raised and why.
        _record(
            session,
            run,
            StageOutcome(
                stage=run.current_stage or "unknown",
                status="FAILED",
                detail=str(exc),
                data={"code": exc.code},
            ),
        )
        _finish(session, run, RunStatus.FAILED.value, error=str(exc))
        return run
    finally:
        locks.release(session, lock, reason=f"run {run.status}")
        audit.record(
            session,
            action="automation.run_finished",
            actor_type=ActorType.SYSTEM if user_id is None else ActorType.USER,
            user_id=user_id,
            channel_id=channel.id,
            entity_type="automation_run",
            entity_id=run.id,
            after={
                "status": run.status,
                "stopped_reason": run.stopped_reason,
                "stages": [entry.get("stage") for entry in run.stages or []],
            },
        )


def _guard(session: Session, channel: Channel, stage: str) -> None:
    """Re-check the stops before every stage.

    Checking once at the start would let a run that began before an emergency stop
    keep rendering and uploading after it.
    """
    from nexora.services import killswitch

    killswitch.require_clear(session)
    automation = _automation(session, channel)
    result = gates.can_automate(session, channel, automation)
    if not result.allowed:
        raise SafetyBlocked(
            "Automation was stopped during this run: "
            + "; ".join(entry["detail"] for entry in result.blockers),
            details=result.to_dict(),
        )


def _run_stage(
    session: Session,
    channel: Channel,
    run: AutomationRun,
    stage: str,
    *,
    user_id: uuid.UUID | None,
) -> StageOutcome:
    handler = {
        "select_topic": _stage_select_topic,
        "research": _stage_research,
        "script": _stage_script,
        "fact_check": _stage_fact_check,
        "voice": _stage_voice,
        "render": _stage_render,
        "thumbnail": _stage_thumbnail,
        "metadata": _stage_metadata,
        "quality_check": _stage_quality,
        "publish": _stage_publish,
    }[stage]
    return handler(session, channel, run, user_id=user_id)


# -------------------------------------------------------------------- the stages
def _stage_select_topic(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    """Pick the best genuinely-relevant, non-duplicate topic for this channel.

    Ranked by the Phase 6 score. Nothing here invents a trend: a candidate must cite
    real collected evidence, and an excluded or insufficiently relevant item is never
    offered.
    """
    from nexora.services import topics as topic_service

    if _selected_candidate_id(run) is not None:
        return skipped("select_topic", "A topic was already selected for this run.")

    candidate = _existing_candidate(session, channel)

    if candidate is None:
        ranked = _ranked_topics(session, channel)
        if not ranked:
            return stopped(
                "select_topic",
                "No collected trend is relevant enough to this channel to build a video "
                f"from (minimum score {MIN_AUTOMATION_SCORE}). Run a trend scan, or "
                "widen the channel's categories.",
            )
        try:
            result = topic_service.generate_candidates(
                session,
                channel,
                count=3,
                actor_type=ActorType.SYSTEM,
                user_id=user_id,
                trend_ids=[row.id for row in ranked[:12]],
            )
        except NexoraError as exc:
            return stopped("select_topic", f"Topic generation is unavailable: {exc}")

        candidate = _first_usable(session, channel, result.candidates)
        if candidate is None:
            return stopped(
                "select_topic",
                "Every proposed topic duplicates something this channel has already "
                "covered or has in progress.",
            )

    duplicate = dedupe.check(session, channel, title=candidate.title, angle=candidate.angle)
    if duplicate.is_duplicate:
        candidate.status = CandidateStatus.REJECTED.value
        candidate.decision_note = duplicate.reason
        session.flush()
        return stopped(
            "select_topic",
            f"The selected topic repeats existing work. {duplicate.reason}",
            duplicate=duplicate.to_dict(),
        )

    # The topic lock is what stops a second worker building this same video.
    try:
        locks.acquire(
            session,
            channel,
            locks.topic_key(duplicate.fingerprint or candidate.title[:64]),
            automation_run_id=run.id,
        )
    except Conflict as exc:
        return stopped("select_topic", f"Another run already claimed this topic. {exc}")

    candidate.status = CandidateStatus.APPROVED.value
    session.flush()

    # The project is created in the research stage, not here: a content project can
    # only be built from a candidate that already has successful research behind it,
    # which is what keeps a script from ever being written without evidence.
    return completed(
        "select_topic",
        f"Selected '{candidate.title}'.",
        topic_candidate_id=str(candidate.id),
        fingerprint=duplicate.fingerprint,
        related=[match.to_dict() for match in duplicate.related],
    )


def _stage_research(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    """Research the selected topic, then build the content project from it.

    The project is created here rather than in selection because a project can only
    be built from a candidate with successful research behind it. That ordering is
    what makes it structurally impossible for a script to be written without evidence.
    """
    from nexora.services import projects as project_service
    from nexora.services.research import engine as research_engine

    if run.content_project_id is not None:
        return skipped("research", "This run already has a researched project.")

    candidate_id = _selected_candidate_id(run)
    if candidate_id is None:
        return stopped("research", "This run selected no topic to research.")
    candidate = session.get(TopicCandidate, candidate_id)
    if candidate is None:
        return stopped("research", "The selected topic candidate no longer exists.")

    result = research_engine.run_research(
        session, channel, candidate, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    if result.research.status != "SUCCESS":
        return stopped(
            "research",
            f"Research did not succeed ({result.research.status}). "
            "A script is never written without it.",
        )

    project = project_service.create_from_candidate(
        session, channel, candidate, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    project.automation_run_id = run.id
    project.topic_fingerprint = dedupe.fingerprint(candidate.title, candidate.angle)
    run.content_project_id = project.id
    session.flush()

    return completed(
        "research",
        f"Collected {result.research.document_count} source documents.",
        document_count=result.research.document_count,
        content_project_id=str(project.id),
    )


def _stage_script(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.services import scripts as script_service

    project = _project(session, run)
    if project.current_script_version_id is not None:
        return skipped("script", "This project already has a script version.")

    result = script_service.generate_script(
        session, channel, project, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    return completed(
        "script",
        f"Wrote version {result.version.version} ({result.version.word_count} words).",
        script_version_id=str(result.version.id),
        version=result.version.version,
    )


def _stage_fact_check(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.db.models import ScriptVersion
    from nexora.services import factcheck as factcheck_service

    project = _project(session, run)
    version = session.get(ScriptVersion, project.current_script_version_id)
    if version is None:
        return stopped("fact_check", "This project has no script version to check.")

    result = factcheck_service.run_fact_check(
        session, channel, project, version, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    counts = factcheck_service.verdict_counts(result.check)
    detail = ", ".join(f"{count} {verdict.lower()}" for verdict, count in counts.items() if count)

    # The run continues even on a failing check: the operator may want the rendered
    # video and a list of claims to fix. The publish stage is where it is refused.
    return completed("fact_check", f"Checked: {detail or 'no checkable claims'}.", **counts)


def _stage_voice(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.db.models import ScriptVersion
    from nexora.services import voice as voice_service

    project = _project(session, run)
    if project.narration_asset_id is not None:
        return skipped("voice", "This project already has narration.")

    version = session.get(ScriptVersion, project.current_script_version_id)
    if version is None:
        return stopped("voice", "This project has no script version to narrate.")

    result = voice_service.generate_narration(
        session, channel, project, version, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    return completed(
        "voice",
        f"Narrated {result.job.duration_seconds:.0f}s "
        f"(timing from {result.job.timing_source}).",
        voice_job_id=str(result.job.id),
    )


def _stage_render(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.services.video import render as render_service

    project = _project(session, run)
    if project.current_render_asset_id is not None:
        return skipped("render", "This project already has a rendered video.")

    result = render_service.render_project(
        session, channel, project, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    if result.job.status != RunStatus.SUCCESS.value:
        return stopped("render", f"Rendering failed: {result.job.error or 'unknown error'}.")
    return completed(
        "render",
        f"Rendered {result.job.resolution}, {result.job.duration_seconds:.0f}s.",
        render_job_id=str(result.job.id),
    )


def _stage_thumbnail(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.services import thumbnails as thumbnail_service

    project = _project(session, run)
    if project.current_thumbnail_id is not None:
        return skipped("thumbnail", "This project already has an approved thumbnail.")

    result = thumbnail_service.generate(
        session, channel, project, actor_type=ActorType.SYSTEM, user_id=user_id
    )
    # Generated, not approved: a thumbnail is a human decision, and automation does not
    # make it. Publishing proceeds without one rather than approving its own work.
    return completed(
        "thumbnail",
        "Generated a thumbnail candidate. It still needs a person to approve it.",
        thumbnail_id=str(result.thumbnail.id),
    )


def _stage_metadata(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.services import metadata as metadata_service

    project = _project(session, run)
    result = metadata_service.generate_metadata(
        session, channel, project, user_id=user_id, actor_type=ActorType.SYSTEM
    )
    return completed(
        "metadata",
        f"Generated title, description and {len(result.version.tags or [])} tags.",
        metadata_version_id=str(result.version.id),
        warnings=result.warnings,
    )


def _stage_quality(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    from nexora.services import quality as quality_service

    project = _project(session, run)
    check = quality_service.run_quality_check(session, channel, project)
    copyright_check = quality_service.run_copyright_check(session, channel, project)
    return completed(
        "quality_check",
        f"Quality {check.status}, copyright {copyright_check.status}.",
        quality_status=check.status,
        copyright_status=copyright_check.status,
    )


def _stage_publish(
    session: Session, channel: Channel, run: AutomationRun, *, user_id: uuid.UUID | None
) -> StageOutcome:
    """The only stage that can reach YouTube, and the most heavily gated.

    Four independent things must all agree: the automation gates, the fact-check gate,
    the publishing preflight, and the daily limit. Automation can never override any
    of them — ``force`` is refused for an autopilot authorization by design.
    """
    from nexora.services import publishing as publishing_service

    project = _project(session, run)
    automation = _automation(session, channel)

    allowed = gates.can_publish_autonomously(session, channel, automation)
    if not allowed.allowed:
        return stopped(
            "publish",
            "Ready for a person to review. Automated publishing is not permitted: "
            + "; ".join(entry["detail"] for entry in allowed.blockers),
            gate=allowed.to_dict(),
        )

    fact_gate = evaluate_fact_gate(session, project, automation)
    if not fact_gate.allowed:
        return stopped("publish", fact_gate.detail, fact_check=fact_gate.to_dict())

    try:
        job = publishing_service.create_publish_job(
            session,
            channel,
            project,
            authorized_by=PublishAuthorization.AUTOPILOT,
            privacy_status="private",
            user_id=user_id,
        )
    except SafetyBlocked as exc:
        return stopped("publish", f"Publishing preflight blocked the upload: {exc}")

    publishing_service.execute_publish(session, job)
    if job.youtube_video_id is None:
        return stopped(
            "publish",
            f"The upload did not complete: {job.last_error or 'no video id was returned'}.",
            publish_job_id=str(job.id),
        )

    return completed(
        "publish",
        f"Published and verified as {job.youtube_video_id}.",
        publish_job_id=str(job.id),
        youtube_video_id=job.youtube_video_id,
        verified=job.verified_at is not None,
    )


# -------------------------------------------------------------------- selection
def _ranked_topics(session: Session, channel: Channel) -> list[TrendingTopic]:
    """This channel's best-ranked, non-excluded trends.

    Uses the Phase 6 relevance rows. There is no second scoring model here: a single
    score that two parts of the system disagreed about would be worse than either.
    """
    rows = session.execute(
        select(TrendingTopic)
        .join(
            ChannelTopicRelevance,
            ChannelTopicRelevance.trending_topic_id == TrendingTopic.id,
        )
        .where(
            ChannelTopicRelevance.channel_id == channel.id,
            ChannelTopicRelevance.status == RelevanceStatus.RELEVANT.value,
            ChannelTopicRelevance.score.is_not(None),
            ChannelTopicRelevance.score >= MIN_AUTOMATION_SCORE,
            TrendingTopic.duplicate_of_id.is_(None),
        )
        .order_by(ChannelTopicRelevance.score.desc())
        .limit(25)
    ).scalars()
    return list(rows)


def _existing_candidate(session: Session, channel: Channel) -> TopicCandidate | None:
    """An operator-approved candidate takes priority over generating a new one.

    If a person approved a topic, automation should build that rather than proposing
    its own — the approval was the instruction.
    """
    return session.execute(
        select(TopicCandidate)
        .where(
            TopicCandidate.channel_id == channel.id,
            TopicCandidate.status == CandidateStatus.APPROVED.value,
        )
        .order_by(TopicCandidate.opportunity_score.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()


def _first_usable(
    session: Session, channel: Channel, candidates: list[TopicCandidate]
) -> TopicCandidate | None:
    for candidate in candidates:
        if not dedupe.check(
            session, channel, title=candidate.title, angle=candidate.angle
        ).is_duplicate:
            return candidate
    return None


# ----------------------------------------------------------------------- helpers
def _selected_candidate_id(run: AutomationRun) -> uuid.UUID | None:
    """The candidate this run picked, read back from its own stage record."""
    for entry in reversed(run.stages or []):
        if entry.get("stage") != "select_topic":
            continue
        raw = (entry.get("data") or {}).get("topic_candidate_id")
        if raw:
            try:
                return uuid.UUID(raw)
            except ValueError:
                return None
    return None


def _project(session: Session, run: AutomationRun) -> ContentProject:
    project = session.get(ContentProject, run.content_project_id) if run.content_project_id else None
    if project is None:
        raise RunStopped(
            stopped(run.current_stage or "unknown", "This run has no content project.")
        )
    return project


def _automation(session: Session, channel: Channel):
    from nexora.services.channels import get_automation_settings

    return get_automation_settings(session, channel.id)


def _record(session: Session, run: AutomationRun, outcome: StageOutcome) -> None:
    run.stages = [*(run.stages or []), outcome.to_dict()]
    session.flush()


def _finish(
    session: Session,
    run: AutomationRun,
    status: str,
    *,
    stopped_reason: str | None = None,
    error: str | None = None,
) -> None:
    run.status = status
    run.stopped_reason = stopped_reason
    run.error = error
    run.finished_at = datetime.now(UTC)
    run.current_stage = None
    session.flush()


def run_to_dict(run: AutomationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "channel_id": str(run.channel_id),
        "content_project_id": str(run.content_project_id) if run.content_project_id else None,
        "mode": run.mode,
        "trigger": run.trigger,
        "status": run.status,
        "current_stage": run.current_stage,
        "stages": run.stages or [],
        "stopped_reason": run.stopped_reason,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "pipeline": list(STAGES),
    }
