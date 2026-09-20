"""Channel profiles, the content-category vocabulary and per-channel trend relevance.

These three tables are what make NEXORA multi-channel. The design rule throughout is
that a channel's identity is **stored configuration**, never something a model guesses:
every relevance decision below can be traced back to a row a person actually set.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexora.db.base import Base, TimestampMixin, utc_column, uuid_pk
from nexora.db.models.enums import RelevanceStatus, ScoreStatus


class ContentCategory(Base, TimestampMixin):
    """One entry in the content vocabulary.

    The vocabulary is **data, not code**. The seeded set spans kids, anime, gaming,
    education, business, technology, news and more, but it is a starting point rather
    than a limit: a channel may add its own categories, and nothing in the pipeline
    treats the seeded keys as special.

    ``keywords`` is what the relevance engine actually matches against, which is why
    the match can always be explained — the keyword that fired is stored on the result.
    """

    __tablename__ = "content_categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: NULL for the shared vocabulary; set for a category one channel invented.
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Terms whose presence in a trend's title or summary counts as a match.
    keywords: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: A hint only. It never sets a channel's audience or made-for-kids declaration.
    audience_hint: Mapped[str | None] = mapped_column(String(32))
    #: Seeded categories cannot be deleted, only overridden by a channel-owned one.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    __table_args__ = (
        UniqueConstraint("channel_id", "key", name="uq_content_categories_channel_key"),
    )


class ChannelProfile(Base, TimestampMixin):
    """Everything NEXORA knows about who a channel is for.

    Separate from :class:`ChannelSettings`, which holds *how a video is made*. This
    holds *what the channel is about and who watches it*, because those are the inputs
    the relevance engine reads.

    ``channels.categories`` remains the channel's **primary** categories;
    ``secondary_categories`` here extends them at a lower weight. Nothing is duplicated
    between the two.
    """

    __tablename__ = "channel_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    #: Free text the operator writes. Matched literally — never used to infer
    #: demographics NEXORA has no way to know.
    audience_description: Mapped[str | None] = mapped_column(Text)
    secondary_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    #: NULL means undeclared. It is never inferred from the channel name or category,
    #: because guessing that a channel is for children is exactly the kind of
    #: assumption that carries legal consequences when it is wrong.
    audience_classification: Mapped[str | None] = mapped_column(String(32))
    country_region: Mapped[str | None] = mapped_column(String(16))

    #: ``channels.primary_language`` stays the primary. These are additional languages
    #: whose items the channel is willing to work from.
    secondary_languages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: Off by default. Translation is a per-channel decision, never automatic.
    translation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    short_form_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    long_form_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferred_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    #: Editorial intent. The hard publishing limits live in AutomationSettings.
    target_videos_per_week: Mapped[int | None] = mapped_column(Integer)

    brand_voice: Mapped[str | None] = mapped_column(Text)
    #: Topics the channel actively wants. Matched as literal phrases.
    preferred_topics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: Topics the channel refuses. A match excludes the item outright.
    blocked_topics: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    content_exclusions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: e.g. "violence", "gambling". Matched the same way as blocked topics.
    sensitive_content_restrictions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    #: Set when a person has been through the profile once. Until then the UI says the
    #: profile is incomplete rather than pretending the defaults were chosen.
    profile_completed_at = utc_column()

    channel = relationship("Channel", back_populates="profile")


class ChannelTopicRelevance(Base):
    """Why one trend is, or is not, worth making a video about *for this channel*.

    One row per (channel, trend). This is the table that lets the same normalized trend
    database serve a kids channel and a technology channel without either seeing the
    other's ranking.

    Every field here is an explanation, not a verdict handed down without reasons:
    ``matched_categories`` names the keyword that fired, ``excluded_by_rules`` names
    the rule the operator configured, and ``score`` is ``None`` whenever the inputs to
    compute it were absent.
    """

    __tablename__ = "channel_topic_relevance"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trending_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trending_topics.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=RelevanceStatus.INSUFFICIENT_DATA.value, index=True
    )
    #: [{category, tier, keywords: [...]}]
    matched_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: [{preference, matched}]
    matched_preferences: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: [{rule, value, detail}] — populated only when the item was excluded.
    excluded_by_rules: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: [{code, detail}] — the human-readable trace, in the order the rules ran.
    relevance_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    #: 0–100 relevance, or NULL when there was nothing to match against.
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    #: The channel-specific Opportunity Score. NULL unless score_status is SCORED.
    score: Mapped[int | None] = mapped_column(Integer, index=True)
    score_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ScoreStatus.INSUFFICIENT_DATA.value
    )
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: How many distinct sources carried this story — counted, never estimated.
    available_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    freshness: Mapped[str | None] = mapped_column(String(16))

    computed_at = utc_column(nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "channel_id", "trending_topic_id", name="uq_channel_topic_relevance_channel_topic"
        ),
        Index("ix_channel_topic_relevance_channel_score", "channel_id", "score"),
        Index("ix_channel_topic_relevance_channel_status", "channel_id", "status"),
    )
