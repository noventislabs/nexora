"""phase 6 channel intelligence

Makes NEXORA multi-channel.

Three things change shape:

* The content-category vocabulary moves out of Python and into ``content_categories``,
  so a kids/anime channel and a finance channel are equally first-class and a channel
  can add categories the seed never anticipated.
* ``channel_profiles`` holds who a channel is for. ``audience_classification`` is
  nullable because it is never inferred — not from the channel name, not from its
  categories, and never from the made-for-kids declaration or into it.
* ``trending_topics.opportunity_score`` is renamed to ``signal_score``. One normalized
  trend row now serves every channel that can see it, so a single score combining
  audience relevance would be a lie. The channel-specific half lives in
  ``channel_topic_relevance``, one row per (channel, trend), with the whole
  explanation attached.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- content vocabulary -----------------------------------------------------
    op.create_table(
        "content_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("audience_hint", sa.String(length=32), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "key", name="uq_content_categories_channel_key"),
    )
    op.create_index(
        op.f("ix_content_categories_channel_id"), "content_categories", ["channel_id"]
    )
    op.create_index(op.f("ix_content_categories_key"), "content_categories", ["key"])

    # Seed the shared vocabulary here so a freshly migrated database is usable
    # immediately. The keyword lists are plain data, imported rather than duplicated so
    # the seed and the application can never drift apart.
    from nexora.services.categories import SEED_CATEGORIES

    op.bulk_insert(
        sa.table(
            "content_categories",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("channel_id", postgresql.UUID(as_uuid=True)),
            sa.column("key", sa.String),
            sa.column("label", sa.String),
            sa.column("description", sa.Text),
            sa.column("keywords", postgresql.JSONB),
            sa.column("audience_hint", sa.String),
            sa.column("is_builtin", sa.Boolean),
            sa.column("sort_order", sa.Integer),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": uuid.uuid4(),
                "channel_id": None,
                "key": key,
                "label": label,
                "description": None,
                "keywords": list(keywords),
                "audience_hint": hint,
                "is_builtin": True,
                "sort_order": order,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            for key, label, hint, order, keywords in SEED_CATEGORIES
        ],
    )

    # --- channel profile --------------------------------------------------------
    op.create_table(
        "channel_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audience_description", sa.Text(), nullable=True),
        sa.Column("secondary_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # Nullable on purpose: undeclared is a real state, distinct from any answer.
        sa.Column("audience_classification", sa.String(length=32), nullable=True),
        sa.Column("country_region", sa.String(length=16), nullable=True),
        sa.Column("secondary_languages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("translation_enabled", sa.Boolean(), nullable=False),
        sa.Column("short_form_enabled", sa.Boolean(), nullable=False),
        sa.Column("long_form_enabled", sa.Boolean(), nullable=False),
        sa.Column("preferred_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("target_videos_per_week", sa.Integer(), nullable=True),
        sa.Column("brand_voice", sa.Text(), nullable=True),
        sa.Column("preferred_topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocked_topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_exclusions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "sensitive_content_restrictions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id"),
    )

    # Every existing channel gets a profile row, all-defaults with
    # audience_classification NULL. Back-filling a value would be inventing an answer
    # to the one question this table exists to make a person answer.
    op.execute(
        "INSERT INTO channel_profiles ("
        "  id, channel_id, secondary_categories, secondary_languages, translation_enabled,"
        "  short_form_enabled, long_form_enabled, preferred_topics, blocked_topics,"
        "  content_exclusions, sensitive_content_restrictions, created_at, updated_at"
        ") SELECT gen_random_uuid(), id, '[]'::jsonb, '[]'::jsonb, false,"
        "  false, true, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, now(), now()"
        " FROM channels"
    )

    # --- shared ingestion --------------------------------------------------------
    op.add_column(
        "trend_sources", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "trend_sources",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="channel"),
    )
    op.create_foreign_key(
        "fk_trend_sources_user_id", "trend_sources", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_trend_sources_user_id"), "trend_sources", ["user_id"])

    op.add_column(
        "trending_topics", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_trending_topics_user_id",
        "trending_topics",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_trending_topics_user_id"), "trending_topics", ["user_id"])
    op.create_index(
        "ix_trending_topics_user_discovered", "trending_topics", ["user_id", "discovered_at"]
    )
    # Existing rows are channel-scoped; give them their channel's owner so shared
    # queries see a consistent world rather than a NULL that means two things.
    op.execute(
        "UPDATE trending_topics SET user_id = channels.user_id "
        "FROM channels WHERE trending_topics.channel_id = channels.id"
    )

    # --- the score on the row is now channel-independent --------------------------
    op.drop_index("ix_trending_topics_channel_score", table_name="trending_topics")
    op.drop_index(op.f("ix_trending_topics_opportunity_score"), table_name="trending_topics")
    op.alter_column("trending_topics", "opportunity_score", new_column_name="signal_score")
    op.alter_column("trending_topics", "score_breakdown", new_column_name="signal_breakdown")
    op.create_index(
        op.f("ix_trending_topics_signal_score"), "trending_topics", ["signal_score"]
    )
    op.create_index(
        "ix_trending_topics_channel_score", "trending_topics", ["channel_id", "signal_score"]
    )
    # The stored breakdown described a score that included audience relevance. Rather
    # than leaving a breakdown whose components no longer match its own score, clear it
    # and let the next scan recompute: a stale explanation is worse than none.
    op.execute("UPDATE trending_topics SET signal_breakdown = NULL, scored_at = NULL")

    # --- per-channel relevance ----------------------------------------------------
    op.create_table(
        "channel_topic_relevance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trending_topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("matched_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("matched_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excluded_by_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("relevance_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_status", sa.String(length=24), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("available_source_count", sa.Integer(), nullable=False),
        sa.Column("freshness", sa.String(length=16), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trending_topic_id"], ["trending_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id", "trending_topic_id", name="uq_channel_topic_relevance_channel_topic"
        ),
    )
    op.create_index(
        op.f("ix_channel_topic_relevance_channel_id"), "channel_topic_relevance", ["channel_id"]
    )
    op.create_index(
        op.f("ix_channel_topic_relevance_trending_topic_id"),
        "channel_topic_relevance",
        ["trending_topic_id"],
    )
    op.create_index(op.f("ix_channel_topic_relevance_score"), "channel_topic_relevance", ["score"])
    op.create_index(op.f("ix_channel_topic_relevance_status"), "channel_topic_relevance", ["status"])
    op.create_index(
        op.f("ix_channel_topic_relevance_computed_at"), "channel_topic_relevance", ["computed_at"]
    )
    op.create_index(
        "ix_channel_topic_relevance_channel_score",
        "channel_topic_relevance",
        ["channel_id", "score"],
    )
    op.create_index(
        "ix_channel_topic_relevance_channel_status",
        "channel_topic_relevance",
        ["channel_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("channel_topic_relevance")

    op.drop_index("ix_trending_topics_channel_score", table_name="trending_topics")
    op.drop_index(op.f("ix_trending_topics_signal_score"), table_name="trending_topics")
    op.alter_column("trending_topics", "signal_score", new_column_name="opportunity_score")
    op.alter_column("trending_topics", "signal_breakdown", new_column_name="score_breakdown")
    op.create_index(
        op.f("ix_trending_topics_opportunity_score"), "trending_topics", ["opportunity_score"]
    )
    op.create_index(
        "ix_trending_topics_channel_score", "trending_topics", ["channel_id", "opportunity_score"]
    )

    op.drop_index("ix_trending_topics_user_discovered", table_name="trending_topics")
    op.drop_index(op.f("ix_trending_topics_user_id"), table_name="trending_topics")
    op.drop_constraint("fk_trending_topics_user_id", "trending_topics", type_="foreignkey")
    op.drop_column("trending_topics", "user_id")

    op.drop_index(op.f("ix_trend_sources_user_id"), table_name="trend_sources")
    op.drop_constraint("fk_trend_sources_user_id", "trend_sources", type_="foreignkey")
    op.drop_column("trend_sources", "scope")
    op.drop_column("trend_sources", "user_id")

    op.drop_table("channel_profiles")
    op.drop_index(op.f("ix_content_categories_key"), table_name="content_categories")
    op.drop_index(op.f("ix_content_categories_channel_id"), table_name="content_categories")
    op.drop_table("content_categories")
