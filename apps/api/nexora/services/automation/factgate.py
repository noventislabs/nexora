"""The fact-check gate on autonomous publishing.

The rule this enforces: **an unverified claim is never turned into a factual
statement by publishing it.**

A person may read a flagged claim and decide it is fine — they can check a source
automation cannot reach, or know the claim from elsewhere. Automation has neither
option, so where a person may proceed with a warning, automation stops.

One documented exception, off by default: a project explicitly marked as commentary on
a channel whose operator enabled the commentary policy. A labelled opinion piece is not
asserting its claims as verified fact. It never applies to the default explainer
format, and enabling it is a deliberate act with its own audit entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import AutomationSettings, ContentProject, FactCheck
from nexora.db.models.enums import CheckStatus
from nexora.services import factcheck as factcheck_service

COMMENTARY_FORMAT = "commentary"


@dataclass
class FactGateResult:
    allowed: bool
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    blocking_claims: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""
    exemption: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "fact_check_status": self.status,
            "verdict_counts": self.counts,
            "blocking_claims": [
                {
                    "assertion": claim.get("assertion"),
                    "verdict": claim.get("verdict"),
                    "reasons": claim.get("reasons", []),
                }
                for claim in self.blocking_claims[:20]
            ],
            "detail": self.detail,
            "exemption": self.exemption,
            "rule": (
                "Automation never publishes a claim the research does not support. A "
                "person may still publish manually after reading the claims below."
            ),
        }


def latest_check(session: Session, project: ContentProject) -> FactCheck | None:
    return session.execute(
        select(FactCheck)
        .where(FactCheck.content_project_id == project.id)
        .order_by(FactCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def evaluate(
    session: Session,
    project: ContentProject,
    automation: AutomationSettings,
) -> FactGateResult:
    """May automation publish this project on the evidence it has?"""
    check = latest_check(session, project)

    if check is None:
        return FactGateResult(
            allowed=False,
            status="NOT_RUN",
            detail=(
                "No fact check has been run for this project. Automation does not "
                "publish unchecked claims."
            ),
        )

    counts = factcheck_service.verdict_counts(check)
    blocking = factcheck_service.blocking_claims(check)

    if not blocking and check.status == CheckStatus.PASS.value:
        return FactGateResult(
            allowed=True,
            status=check.status,
            counts=counts,
            detail=(
                f"Every one of {counts[factcheck_service.SUPPORTED]} checkable claims "
                "is supported by a research document that exists."
            ),
        )

    if not blocking:
        # Only overstatements. The claims are true; the wording is stronger than the
        # research. Automation may proceed where the channel requires no fact-check
        # pass, because nothing here is unsupported.
        if automation.require_fact_check_pass:
            return FactGateResult(
                allowed=False,
                status=check.status,
                counts=counts,
                detail=(
                    f"{counts[factcheck_service.PARTIALLY_SUPPORTED]} claim(s) state "
                    "the research more strongly than it supports, and this channel "
                    "requires a clean fact check before automated publishing."
                ),
            )
        return FactGateResult(
            allowed=True,
            status=check.status,
            counts=counts,
            detail=(
                "No claim is unsupported. Some are stated more strongly than the "
                "research, and this channel does not require a clean pass."
            ),
        )

    # --- there are blocking claims ---------------------------------------------
    uncheckable = counts[factcheck_service.INSUFFICIENT_SOURCES]
    contradicted = counts[factcheck_service.CONTRADICTED]
    unverified = counts[factcheck_service.UNVERIFIED]

    if (
        automation.allow_unverified_commentary
        and project.editorial_format == COMMENTARY_FORMAT
        and contradicted == 0
    ):
        # Commentary may rest on claims the research does not establish, because it is
        # labelled as opinion rather than asserted as fact. A *contradicted* claim is
        # still blocked: the sources actively disagree, and calling that opinion would
        # be using the label to get around the evidence.
        return FactGateResult(
            allowed=True,
            status=check.status,
            counts=counts,
            blocking_claims=blocking,
            detail=(
                f"{unverified + uncheckable} claim(s) are not established by the "
                "research. This project is marked as commentary and this channel "
                "permits that, so it is published as labelled opinion rather than as "
                "verified fact."
            ),
            exemption="commentary_format",
        )

    parts = []
    if contradicted:
        parts.append(f"{contradicted} contradicted by the research")
    if unverified:
        parts.append(f"{unverified} unsupported by any source")
    if uncheckable:
        parts.append(f"{uncheckable} not checkable for lack of sources")

    return FactGateResult(
        allowed=False,
        status=check.status,
        counts=counts,
        blocking_claims=blocking,
        detail=(
            "Automated publishing is blocked: " + ", ".join(parts) + ". "
            "Publishing these would turn unverified claims into factual statements."
        ),
    )
