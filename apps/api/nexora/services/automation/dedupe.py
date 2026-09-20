"""Topic deduplication — deterministic, never a random similarity score.

Automation that runs daily will eventually be handed the same story twice: a trend
re-published by a second outlet, a follow-up article, a topic candidate generated from
overlapping evidence. Producing the second video wastes a day of the channel's publish
budget and looks, to a viewer, like the channel repeating itself.

The check is Jaccard overlap on a fingerprint of significant tokens. It is deterministic
and reproducible: the same two titles always produce the same number, and the number can
be recomputed by hand from the stored fingerprints. Nothing here is a model's opinion
about whether two topics are "basically the same".
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexora.db.models import Channel, ContentProject, TopicCandidate, YouTubeVideo
from nexora.services.trends.scoring import tokenize

#: Two topics count as the same story at or above this overlap of significant tokens.
#: Tuned to catch restatements ("X launches Y" / "Y launched by X") without collapsing
#: genuinely different stories that share a subject.
DUPLICATE_THRESHOLD = 0.6

#: Below this, the two are reported as related rather than duplicate: worth an
#: operator's attention, not worth blocking automatically.
RELATED_THRESHOLD = 0.4

#: How far back a published video still counts as ground already covered.
PUBLISHED_LOOKBACK = timedelta(days=90)
#: An in-flight or recently rejected project blocks for a shorter window.
PROJECT_LOOKBACK = timedelta(days=45)

#: A fingerprint needs this many significant tokens to be meaningful. A two-word title
#: would collide with everything.
MIN_TOKENS = 3


@dataclass
class DuplicateMatch:
    kind: str
    id: uuid.UUID
    title: str
    similarity: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "title": self.title,
            "similarity": round(self.similarity, 3),
            "detail": self.detail,
        }


@dataclass
class DuplicateCheck:
    """The verdict, and everything it was based on."""

    is_duplicate: bool
    fingerprint: str | None
    tokens: list[str] = field(default_factory=list)
    duplicates: list[DuplicateMatch] = field(default_factory=list)
    related: list[DuplicateMatch] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_duplicate": self.is_duplicate,
            "fingerprint": self.fingerprint,
            "significant_tokens": self.tokens,
            "duplicates": [match.to_dict() for match in self.duplicates],
            "related": [match.to_dict() for match in self.related],
            "reason": self.reason,
            "method": (
                "Jaccard overlap of significant title and angle tokens against this "
                f"channel's own projects and published videos. At or above "
                f"{DUPLICATE_THRESHOLD} the topics are treated as the same story. "
                "Deterministic: the same inputs always give the same number."
            ),
        }


def significant_tokens(*parts: str | None) -> list[str]:
    """The vocabulary a fingerprint is built from, ordered for reproducibility."""
    text = " ".join(part for part in parts if part)
    return sorted(tokenize(text))


def fingerprint(*parts: str | None) -> str | None:
    """A stable hash of a topic's significant vocabulary.

    ``None`` when there is too little to identify: a fingerprint of one or two common
    words would match unrelated topics, and a false duplicate silently drops a video
    the channel wanted.
    """
    tokens = significant_tokens(*parts)
    if len(tokens) < MIN_TOKENS:
        return None
    return hashlib.sha256(" ".join(tokens).encode()).hexdigest()


def similarity(left: list[str], right: list[str]) -> float:
    """Jaccard overlap of two token sets. 1.0 is identical vocabulary, 0.0 disjoint."""
    if not left or not right:
        return 0.0
    first, second = set(left), set(right)
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def check(
    session: Session,
    channel: Channel,
    *,
    title: str,
    angle: str | None = None,
    exclude_project_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> DuplicateCheck:
    """Would this topic repeat ground this channel has already covered?

    Scoped to one channel throughout. Two channels covering the same story is normal
    and expected — it is the same channel doing it twice that is the problem.
    """
    now = now or datetime.now(UTC)
    tokens = significant_tokens(title, angle)
    digest = fingerprint(title, angle)

    if digest is None:
        return DuplicateCheck(
            is_duplicate=False,
            fingerprint=None,
            tokens=tokens,
            reason=(
                f"Too few distinctive words ({len(tokens)}) to fingerprint this topic, "
                f"so it cannot be compared. At least {MIN_TOKENS} are needed."
            ),
        )

    duplicates: list[DuplicateMatch] = []
    related: list[DuplicateMatch] = []

    def consider(match: DuplicateMatch) -> None:
        if match.similarity >= DUPLICATE_THRESHOLD:
            duplicates.append(match)
        elif match.similarity >= RELATED_THRESHOLD:
            related.append(match)

    # --- an identical fingerprint is a duplicate without further comparison -------
    exact = session.execute(
        select(ContentProject).where(
            ContentProject.channel_id == channel.id,
            ContentProject.topic_fingerprint == digest,
            ContentProject.id != (exclude_project_id or uuid.UUID(int=0)),
        )
    ).scalars()
    for project in exact:
        duplicates.append(
            DuplicateMatch(
                kind="content_project",
                id=project.id,
                title=project.title,
                similarity=1.0,
                detail=(
                    f"An existing project ({project.status}) has an identical topic "
                    "fingerprint."
                ),
            )
        )

    # --- projects in flight or recently decided ----------------------------------
    project_rows = session.execute(
        select(ContentProject).where(
            ContentProject.channel_id == channel.id,
            ContentProject.created_at >= now - PROJECT_LOOKBACK,
            ContentProject.id != (exclude_project_id or uuid.UUID(int=0)),
        )
    ).scalars()
    seen: set[uuid.UUID] = {match.id for match in duplicates}
    for project in project_rows:
        if project.id in seen:
            continue
        score = similarity(tokens, significant_tokens(project.title))
        consider(
            DuplicateMatch(
                kind="content_project",
                id=project.id,
                title=project.title,
                similarity=score,
                detail=f"An existing project in status '{project.status}'.",
            )
        )

    # --- videos this channel has already published --------------------------------
    for video in session.execute(
        select(YouTubeVideo).where(
            YouTubeVideo.channel_id == channel.id,
            YouTubeVideo.published_at.is_not(None),
            YouTubeVideo.published_at >= now - PUBLISHED_LOOKBACK,
        )
    ).scalars():
        score = similarity(tokens, significant_tokens(video.title))
        consider(
            DuplicateMatch(
                kind="published_video",
                id=video.id,
                title=video.title or "",
                similarity=score,
                detail=(
                    "Already published by this channel on "
                    f"{video.published_at.date() if video.published_at else 'an unknown date'}."
                ),
            )
        )

    # --- topics an operator already rejected ---------------------------------------
    for candidate in session.execute(
        select(TopicCandidate).where(
            TopicCandidate.channel_id == channel.id,
            TopicCandidate.status == "rejected",
            TopicCandidate.created_at >= now - PROJECT_LOOKBACK,
        )
    ).scalars():
        score = similarity(tokens, significant_tokens(candidate.title, candidate.angle))
        if score >= DUPLICATE_THRESHOLD:
            duplicates.append(
                DuplicateMatch(
                    kind="rejected_topic",
                    id=candidate.id,
                    title=candidate.title,
                    similarity=score,
                    detail=(
                        "An operator already rejected this topic for this channel. "
                        "Automation does not re-propose a rejected topic."
                    ),
                )
            )

    duplicates.sort(key=lambda match: match.similarity, reverse=True)
    related.sort(key=lambda match: match.similarity, reverse=True)

    return DuplicateCheck(
        is_duplicate=bool(duplicates),
        fingerprint=digest,
        tokens=tokens,
        duplicates=duplicates,
        related=related[:5],
        reason=(
            f"Overlaps {duplicates[0].similarity:.0%} with "
            f"'{duplicates[0].title[:80]}' ({duplicates[0].kind.replace('_', ' ')})."
            if duplicates
            else None
        ),
    )
