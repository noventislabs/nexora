"""Quality and copyright gates.

Both are **deterministic**. A model is never asked to score the work, because a score
a model invents is exactly the fabrication this product refuses. Every check below is
a rule over facts already recorded: measured durations, counted documents, licence
statuses, fact-check verdicts and metadata lengths.

Each check reports whether it passed and why, so a blocked publish always names its
cause.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import (
    Channel,
    ContentProject,
    CopyrightCheck,
    FactCheck,
    QualityCheck,
    ScriptVersion,
    VideoAsset,
    VideoRenderJob,
    VoiceJob,
)
from nexora.db.models.enums import CheckStatus, LicenseStatus, RiskLevel, RunStatus
from nexora.services import audit

#: How far the measured render may drift from the project's target before it is flagged.
DURATION_TOLERANCE = 0.35

RISK_ORDER = {RiskLevel.LOW.value: 0, RiskLevel.MEDIUM.value: 1, RiskLevel.HIGH.value: 2}


@dataclass
class Check:
    key: str
    label: str
    passed: bool
    detail: str
    blocking: bool = True
    measured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "passed": self.passed,
            "blocking": self.blocking,
            "detail": self.detail,
            "measured": self.measured,
        }


@dataclass
class CheckResult:
    status: CheckStatus
    checks: list[Check]
    score: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed and check.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if not check.passed and not check.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            "failures": [check.key for check in self.failures],
            "warnings": [check.key for check in self.warnings],
            "details": self.details,
        }


def run_quality_check(
    session: Session, channel: Channel, project: ContentProject
) -> QualityCheck:
    """Gate the produced artefacts against the channel's configured thresholds."""
    from nexora.services.channels import get_automation_settings, get_channel_settings

    automation = get_automation_settings(session, channel.id)
    settings_row = get_channel_settings(session, channel.id)
    checks: list[Check] = []

    version = (
        session.get(ScriptVersion, project.current_script_version_id)
        if project.current_script_version_id
        else None
    )
    checks.append(
        Check(
            key="script_present",
            label="Script selected",
            passed=version is not None,
            detail=(
                f"Script v{version.version} is selected."
                if version
                else "No script version is selected for this project."
            ),
        )
    )

    narration = _latest_voice_job(session, project)
    checks.append(
        Check(
            key="narration_present",
            label="Narration produced",
            passed=narration is not None,
            detail=(
                f"Narration measured at {narration.duration_seconds:.1f}s."
                if narration and narration.duration_seconds
                else "No successful narration exists for this project."
            ),
            measured={"duration_seconds": narration.duration_seconds} if narration else {},
        )
    )

    render = _latest_render(session, project)
    render_asset = (
        session.get(VideoAsset, render.output_asset_id)
        if render and render.output_asset_id
        else None
    )
    checks.append(
        Check(
            key="render_present",
            label="Video rendered",
            passed=render_asset is not None,
            detail=(
                f"Rendered {render.resolution} at {render.duration_seconds:.1f}s "
                f"({render.output_bytes} bytes)."
                if render and render.duration_seconds
                else "No successful render exists for this project."
            ),
            measured=(
                {
                    "duration_seconds": render.duration_seconds,
                    "resolution": render.resolution,
                    "bytes": render.output_bytes,
                }
                if render
                else {}
            ),
        )
    )

    # Duration: compare the *measured* render against the project's target.
    if render and render.duration_seconds:
        target = project.target_duration_seconds
        drift = abs(render.duration_seconds - target) / max(target, 1)
        checks.append(
            Check(
                key="duration_within_target",
                label="Duration close to target",
                passed=drift <= DURATION_TOLERANCE,
                blocking=False,
                detail=(
                    f"Measured {render.duration_seconds:.0f}s against a {target}s target "
                    f"({drift:.0%} drift). This is a warning, not a block."
                ),
                measured={"measured_seconds": render.duration_seconds, "target_seconds": target},
            )
        )

    # Originality, recomputed from the stored sources rather than trusted from earlier.
    #
    # Two distinct outcomes, and they must not be conflated:
    #   conclusive + below threshold -> a real failure, and blocking.
    #   inconclusive (nothing to compare against) -> unverified. Reported as a warning
    #   rather than a block, because a project with no stored sources could otherwise
    #   never publish at all. The wording never claims it passed.
    originality = _originality(session, project, version)
    if originality["conclusive"]:
        checks.append(
            Check(
                key="originality",
                label="Originality",
                passed=originality["score"] >= automation.min_originality_score,
                detail=(
                    f"{originality['score']}/100 against {originality['checked_documents']} "
                    f"source excerpt(s); the channel requires "
                    f"{automation.min_originality_score}."
                ),
                measured=originality,
            )
        )
    else:
        checks.append(
            Check(
                key="originality",
                label="Originality",
                passed=False,
                blocking=False,
                detail=(
                    "UNVERIFIED — no source text was available to compare against, so "
                    "originality could not be established. This is not a pass."
                ),
                measured=originality,
            )
        )

    # Fact check, if the channel requires it.
    fact_check = _latest_fact_check(session, project)
    if automation.require_fact_check_pass:
        checks.append(
            Check(
                key="fact_check",
                label="Fact check",
                passed=fact_check is not None and fact_check.status == CheckStatus.PASS.value,
                detail=(
                    f"Fact check is {fact_check.status}: {fact_check.supported_count} supported, "
                    f"{fact_check.needs_review_count} need review, "
                    f"{fact_check.unsupported_count} unsupported."
                    if fact_check
                    else "This project has not been fact checked."
                ),
                measured=(
                    {
                        "status": fact_check.status,
                        "unsupported": fact_check.unsupported_count,
                        "needs_review": fact_check.needs_review_count,
                    }
                    if fact_check
                    else {}
                ),
            )
        )

    # Made-for-kids must be a deliberate declaration, never a default.
    checks.append(
        Check(
            key="made_for_kids_declared",
            label="Made-for-kids declared",
            passed=settings_row.made_for_kids_default is not None,
            detail=(
                f"Declared as {'made for kids' if settings_row.made_for_kids_default else 'not made for kids'}."
                if settings_row.made_for_kids_default is not None
                else (
                    "YouTube requires every upload to declare whether it is made for children, "
                    "and the declaration carries legal weight. Set it explicitly in channel "
                    "settings — NEXORA will not guess."
                )
            ),
        )
    )

    thumbnail_ok = project.current_thumbnail_id is not None
    checks.append(
        Check(
            key="thumbnail_approved",
            label="Thumbnail approved",
            passed=thumbnail_ok,
            blocking=False,
            detail=(
                "An approved thumbnail is selected."
                if thumbnail_ok
                else "No thumbnail is approved; YouTube will auto-generate one."
            ),
        )
    )

    blocking_total = sum(1 for check in checks if check.blocking)
    blocking_passed = sum(1 for check in checks if check.blocking and check.passed)
    score = round(100 * blocking_passed / blocking_total) if blocking_total else None

    failures = [check for check in checks if check.blocking and not check.passed]
    warnings = [check for check in checks if not check.blocking and not check.passed]
    if failures:
        status = CheckStatus.FAIL
    elif warnings or (score is not None and score < automation.min_quality_score):
        status = CheckStatus.REVIEW
    else:
        status = CheckStatus.PASS

    result = CheckResult(status=status, checks=checks, score=score)
    record = QualityCheck(
        content_project_id=project.id,
        kind="publish_preflight",
        status=status.value,
        score=score,
        checks=[check.to_dict() for check in checks],
        details={"min_quality_score": automation.min_quality_score},
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()

    audit.record(
        session,
        action="quality_check.completed",
        channel_id=channel.id,
        entity_type="quality_check",
        entity_id=record.id,
        summary=(
            f"Quality {status.value} ({score}/100): "
            + (", ".join(check.key for check in failures) or "no blocking failures")
        ),
        after=result.to_dict(),
    )
    return record


def run_copyright_check(
    session: Session, channel: Channel, project: ContentProject
) -> CopyrightCheck:
    """Audit every asset that reaches the render for an established licence."""
    from nexora.services.channels import get_automation_settings

    automation = get_automation_settings(session, channel.id)
    assets = list(
        session.execute(
            select(VideoAsset).where(VideoAsset.content_project_id == project.id)
        ).scalars()
    )

    findings: list[dict[str, Any]] = []
    unknown = 0
    prohibited = 0
    for asset in assets:
        if asset.license_status == LicenseStatus.PROHIBITED.value:
            prohibited += 1
        elif asset.license_status != LicenseStatus.PERMITTED.value:
            unknown += 1
        else:
            continue
        findings.append(
            {
                "asset_id": str(asset.id),
                "kind": asset.kind,
                "license_type": asset.license_type,
                "license_status": asset.license_status,
                "source": asset.source,
                "source_url": asset.source_url,
                "note": (
                    "This asset's licence is not established. Record its licence, or remove "
                    "it, before publishing."
                ),
            }
        )

    if prohibited:
        risk = RiskLevel.HIGH.value
    elif unknown:
        risk = RiskLevel.MEDIUM.value
    elif assets:
        risk = RiskLevel.LOW.value
    else:
        # Nothing to audit is not the same as audited and clean.
        risk = RiskLevel.UNKNOWN.value

    blocks = prohibited > 0 or (unknown > 0 and automation.block_on_unknown_license)
    over_threshold = RISK_ORDER.get(risk, 99) > RISK_ORDER.get(automation.max_copyright_risk, 0)

    if blocks or risk == RiskLevel.UNKNOWN.value:
        status = CheckStatus.FAIL if blocks else CheckStatus.REVIEW
    elif over_threshold:
        status = CheckStatus.REVIEW
    else:
        status = CheckStatus.PASS

    record = CopyrightCheck(
        content_project_id=project.id,
        status=status.value,
        risk_level=risk,
        unknown_license_count=unknown,
        prohibited_count=prohibited,
        findings=findings,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()

    audit.record(
        session,
        action="copyright_check.completed",
        channel_id=channel.id,
        entity_type="copyright_check",
        entity_id=record.id,
        summary=(
            f"Copyright {status.value}, risk {risk}: {len(assets)} asset(s), "
            f"{unknown} with an unestablished licence, {prohibited} prohibited."
        ),
    )
    return record


def _latest_voice_job(session: Session, project: ContentProject) -> VoiceJob | None:
    return session.execute(
        select(VoiceJob)
        .where(
            VoiceJob.content_project_id == project.id,
            VoiceJob.status == RunStatus.SUCCESS.value,
        )
        .order_by(VoiceJob.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_render(session: Session, project: ContentProject) -> VideoRenderJob | None:
    from nexora.db.models import VideoProject

    video_project = session.execute(
        select(VideoProject).where(VideoProject.content_project_id == project.id)
    ).scalar_one_or_none()
    if video_project is None:
        return None
    return session.execute(
        select(VideoRenderJob)
        .where(
            VideoRenderJob.video_project_id == video_project.id,
            VideoRenderJob.status == RunStatus.SUCCESS.value,
        )
        .order_by(VideoRenderJob.finished_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_fact_check(session: Session, project: ContentProject) -> FactCheck | None:
    return session.execute(
        select(FactCheck)
        .where(FactCheck.content_project_id == project.id)
        .order_by(FactCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _originality(
    session: Session, project: ContentProject, version: ScriptVersion | None
) -> dict[str, Any]:
    from nexora.db.models import ResearchDocument
    from nexora.services.scripts import check_originality

    if version is None:
        return {"score": 0, "conclusive": False, "checked_documents": 0, "matches": []}
    documents = (
        list(
            session.execute(
                select(ResearchDocument).where(ResearchDocument.research_id == project.research_id)
            ).scalars()
        )
        if project.research_id
        else []
    )
    return check_originality(version.narration_text or "", documents)


def latest_quality_check(session: Session, project_id: uuid.UUID) -> QualityCheck | None:
    return session.execute(
        select(QualityCheck)
        .where(QualityCheck.content_project_id == project_id)
        .order_by(QualityCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def latest_copyright_check(session: Session, project_id: uuid.UUID) -> CopyrightCheck | None:
    return session.execute(
        select(CopyrightCheck)
        .where(CopyrightCheck.content_project_id == project_id)
        .order_by(CopyrightCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def quality_to_dict(record: QualityCheck) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "kind": record.kind,
        "status": record.status,
        "score": record.score,
        "checks": record.checks or [],
        "details": record.details or {},
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "blocks_publishing": record.status == CheckStatus.FAIL.value,
    }


def copyright_to_dict(record: CopyrightCheck) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "status": record.status,
        "risk_level": record.risk_level,
        "unknown_license_count": record.unknown_license_count,
        "prohibited_count": record.prohibited_count,
        "findings": record.findings or [],
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "blocks_publishing": record.status == CheckStatus.FAIL.value,
    }


