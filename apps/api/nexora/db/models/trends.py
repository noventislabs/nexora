"""Trend sources, normalized trending topics and topic candidates."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import CandidateStatus


class TrendSource(Base, TimestampMixin):
    """A configured provider instance (one RSS feed, one subreddit, one API region)."""

    __tablename__ = "trend_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reliability: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    last_run_at = utc_column()
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_item_count: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("channel_id", "kind", "name", name="uq_trend_sources_channel_kind_name"),
    )


class TrendingTopic(Base):
    """A single trend observation normalized into the common schema.

    Fields that a source does not provide stay NULL. They are never defaulted to zero,
    because ``0 views`` and ``views unknown`` are different facts.
    """

    __tablename__ = "trending_topics"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trend_sources.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    source_name: Mapped[str] = mapped_column(String(160), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str | None] = mapped_column(String(16))
    author: Mapped[str | None] = mapped_column(String(255))
    published_at = utc_column(index=True)
    collected_at = utc_column(nullable=False)

    # Engagement is a sparse map — only keys the source actually returned.
    engagement: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    opportunity_score: Mapped[int | None] = mapped_column(Integer, index=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scored_at = utc_column()

    __table_args__ = (
        UniqueConstraint("source_id", "dedupe_hash", name="uq_trending_topics_source_dedupe"),
        Index("ix_trending_topics_channel_collected", "channel_id", "collected_at"),
    )


class TopicCandidate(Base, TimestampMixin):
    """An LLM-proposed video topic, always traceable to its evidence."""

    __tablename__ = "topic_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trending_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trending_topics.id", ondelete="SET NULL"), index=True
    )
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    why_now: Mapped[str | None] = mapped_column(Text)
    risks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    opportunity_score: Mapped[int | None] = mapped_column(Integer, index=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    competition_level: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CandidateStatus.PROPOSED.value, index=True
    )
    decided_at = utc_column()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision_note: Mapped[str | None] = mapped_column(Text)

    generated_by_provider: Mapped[str | None] = mapped_column(String(48))
    generated_by_model: Mapped[str | None] = mapped_column(String(96))


class TopicResearch(Base, TimestampMixin):
    """Evidence gathered for a candidate before any script is written."""

    __tablename__ = "topic_research"

    id: Mapped[uuid.UUID] = uuid_pk()
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    summary: Mapped[str | None] = mapped_column(Text)

    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    key_facts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    claims: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    entities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    statistics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    uncertainties: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    conflicts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    provider: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(96))
    error: Mapped[str | None] = mapped_column(Text)
