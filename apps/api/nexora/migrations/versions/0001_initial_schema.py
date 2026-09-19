"""initial schema

Creates every core table for NEXORA AI AUTOPILOT.

This migration is an explicit, frozen snapshot of the schema at Phase 1. It
deliberately does NOT call ``Base.metadata.create_all``: a migration that reflects the
live models would silently change meaning every time a model changes, and later
migrations would then collide with columns it had already created.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+; pgcrypto covers older servers.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('auth_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('csrf_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions'))
    )
    op.create_index(op.f('ix_auth_sessions_expires_at'), 'auth_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_auth_sessions_token_fingerprint'), 'auth_sessions', ['token_fingerprint'], unique=True)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('channels',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('primary_language', sa.String(length=16), nullable=False),
    sa.Column('secondary_language', sa.String(length=16), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('branding', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_channels_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_channels')),
    sa.UniqueConstraint('user_id', 'slug', name='uq_channels_user_slug')
    )
    op.create_index(op.f('ix_channels_slug'), 'channels', ['slug'], unique=False)
    op.create_index(op.f('ix_channels_user_id'), 'channels', ['user_id'], unique=False)
    op.create_table('system_settings',
    sa.Column('key', sa.String(length=96), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_system_settings_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_system_settings'))
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('channel_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=96), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_audit_logs_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_channel_id'), 'audit_logs', ['channel_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_entity_id'), 'audit_logs', ['entity_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    op.create_table('automation_settings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('mode', sa.String(length=32), nullable=False),
    sa.Column('autopilot_enabled', sa.Boolean(), nullable=False),
    sa.Column('auto_publish_enabled', sa.Boolean(), nullable=False),
    sa.Column('require_human_approval', sa.Boolean(), nullable=False),
    sa.Column('max_videos_per_day', sa.Integer(), nullable=False),
    sa.Column('max_videos_per_week', sa.Integer(), nullable=False),
    sa.Column('min_interval_minutes', sa.Integer(), nullable=False),
    sa.Column('publish_window_start_hour', sa.Integer(), nullable=False),
    sa.Column('publish_window_end_hour', sa.Integer(), nullable=False),
    sa.Column('preferred_publish_hour', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('min_quality_score', sa.Integer(), nullable=False),
    sa.Column('min_originality_score', sa.Integer(), nullable=False),
    sa.Column('max_copyright_risk', sa.String(length=16), nullable=False),
    sa.Column('block_on_unknown_license', sa.Boolean(), nullable=False),
    sa.Column('require_fact_check_pass', sa.Boolean(), nullable=False),
    sa.Column('emergency_stop', sa.Boolean(), nullable=False),
    sa.Column('emergency_stop_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('emergency_stop_reason', sa.Text(), nullable=True),
    sa.Column('daily_scan_enabled', sa.Boolean(), nullable=False),
    sa.Column('daily_scan_hour', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_automation_settings_channel_id_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_settings')),
    sa.UniqueConstraint('channel_id', name=op.f('uq_automation_settings_channel_id'))
    )
    op.create_table('channel_settings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('default_video_format', sa.String(length=32), nullable=False),
    sa.Column('target_duration_min_seconds', sa.Integer(), nullable=False),
    sa.Column('target_duration_max_seconds', sa.Integer(), nullable=False),
    sa.Column('narration_tone', sa.String(length=64), nullable=False),
    sa.Column('call_to_action', sa.Text(), nullable=True),
    sa.Column('aspect_ratio', sa.String(length=16), nullable=False),
    sa.Column('resolution', sa.String(length=16), nullable=False),
    sa.Column('subtitle_burn_in', sa.Boolean(), nullable=False),
    sa.Column('preferred_voice_id', sa.String(length=128), nullable=True),
    sa.Column('editorial_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_channel_settings_channel_id_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_channel_settings')),
    sa.UniqueConstraint('channel_id', name=op.f('uq_channel_settings_channel_id'))
    )
    op.create_table('oauth_states',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('state_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('code_verifier_encrypted', sa.Text(), nullable=True),
    sa.Column('redirect_to', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_oauth_states_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_oauth_states_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_states'))
    )
    op.create_index(op.f('ix_oauth_states_expires_at'), 'oauth_states', ['expires_at'], unique=False)
    op.create_index(op.f('ix_oauth_states_state_fingerprint'), 'oauth_states', ['state_fingerprint'], unique=True)
    op.create_table('trend_sources',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('reliability', sa.Float(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=32), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_item_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_trend_sources_channel_id_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trend_sources')),
    sa.UniqueConstraint('channel_id', 'kind', 'name', name='uq_trend_sources_channel_kind_name')
    )
    op.create_index(op.f('ix_trend_sources_channel_id'), 'trend_sources', ['channel_id'], unique=False)
    op.create_table('youtube_connections',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('youtube_channel_id', sa.String(length=64), nullable=True),
    sa.Column('youtube_channel_title', sa.String(length=255), nullable=True),
    sa.Column('youtube_custom_url', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('access_token_encrypted', sa.Text(), nullable=True),
    sa.Column('refresh_token_encrypted', sa.Text(), nullable=True),
    sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('has_analytics_scope', sa.Boolean(), nullable=False),
    sa.Column('has_monetary_scope', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_youtube_connections_channel_id_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_youtube_connections')),
    sa.UniqueConstraint('channel_id', name=op.f('uq_youtube_connections_channel_id'))
    )
    op.create_index(op.f('ix_youtube_connections_youtube_channel_id'), 'youtube_connections', ['youtube_channel_id'], unique=False)
    op.create_table('trending_topics',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=True),
    sa.Column('source_kind', sa.String(length=48), nullable=False),
    sa.Column('source_name', sa.String(length=160), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('dedupe_hash', sa.String(length=64), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('language', sa.String(length=16), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('engagement', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('opportunity_score', sa.Integer(), nullable=True),
    sa.Column('score_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_trending_topics_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['trend_sources.id'], name=op.f('fk_trending_topics_source_id_trend_sources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trending_topics')),
    sa.UniqueConstraint('source_id', 'dedupe_hash', name='uq_trending_topics_source_dedupe')
    )
    op.create_index(op.f('ix_trending_topics_category'), 'trending_topics', ['category'], unique=False)
    op.create_index('ix_trending_topics_channel_collected', 'trending_topics', ['channel_id', 'collected_at'], unique=False)
    op.create_index(op.f('ix_trending_topics_channel_id'), 'trending_topics', ['channel_id'], unique=False)
    op.create_index(op.f('ix_trending_topics_opportunity_score'), 'trending_topics', ['opportunity_score'], unique=False)
    op.create_index(op.f('ix_trending_topics_published_at'), 'trending_topics', ['published_at'], unique=False)
    op.create_index(op.f('ix_trending_topics_source_id'), 'trending_topics', ['source_id'], unique=False)
    op.create_table('topic_candidates',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('trending_topic_id', sa.UUID(), nullable=True),
    sa.Column('generation_run_id', sa.Uuid(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('angle', sa.Text(), nullable=False),
    sa.Column('audience', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('why_now', sa.Text(), nullable=True),
    sa.Column('risks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('opportunity_score', sa.Integer(), nullable=True),
    sa.Column('score_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('competition_level', sa.String(length=16), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('generated_by_provider', sa.String(length=48), nullable=True),
    sa.Column('generated_by_model', sa.String(length=96), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_topic_candidates_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_topic_candidates_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['trending_topic_id'], ['trending_topics.id'], name=op.f('fk_topic_candidates_trending_topic_id_trending_topics'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topic_candidates'))
    )
    op.create_index(op.f('ix_topic_candidates_channel_id'), 'topic_candidates', ['channel_id'], unique=False)
    op.create_index(op.f('ix_topic_candidates_generation_run_id'), 'topic_candidates', ['generation_run_id'], unique=False)
    op.create_index(op.f('ix_topic_candidates_opportunity_score'), 'topic_candidates', ['opportunity_score'], unique=False)
    op.create_index(op.f('ix_topic_candidates_status'), 'topic_candidates', ['status'], unique=False)
    op.create_index(op.f('ix_topic_candidates_trending_topic_id'), 'topic_candidates', ['trending_topic_id'], unique=False)
    op.create_table('topic_research',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('topic_candidate_id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('key_facts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('claims', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('statistics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('uncertainties', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('conflicts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('provider', sa.String(length=48), nullable=True),
    sa.Column('model', sa.String(length=96), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_topic_research_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['topic_candidate_id'], ['topic_candidates.id'], name=op.f('fk_topic_research_topic_candidate_id_topic_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topic_research'))
    )
    op.create_index(op.f('ix_topic_research_channel_id'), 'topic_research', ['channel_id'], unique=False)
    op.create_index(op.f('ix_topic_research_topic_candidate_id'), 'topic_research', ['topic_candidate_id'], unique=False)
    op.create_table('content_projects',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('topic_candidate_id', sa.UUID(), nullable=True),
    sa.Column('research_id', sa.UUID(), nullable=True),
    sa.Column('automation_run_id', sa.Uuid(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('video_format', sa.String(length=32), nullable=False),
    sa.Column('target_duration_seconds', sa.Integer(), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('current_script_version_id', sa.Uuid(), nullable=True),
    sa.Column('current_metadata_version_id', sa.Uuid(), nullable=True),
    sa.Column('current_thumbnail_id', sa.Uuid(), nullable=True),
    sa.Column('current_render_asset_id', sa.Uuid(), nullable=True),
    sa.Column('narration_asset_id', sa.Uuid(), nullable=True),
    sa.Column('approval_status', sa.String(length=16), nullable=False),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_content_projects_approved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_content_projects_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['research_id'], ['topic_research.id'], name=op.f('fk_content_projects_research_id_topic_research'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['topic_candidate_id'], ['topic_candidates.id'], name=op.f('fk_content_projects_topic_candidate_id_topic_candidates'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_projects'))
    )
    op.create_index(op.f('ix_content_projects_automation_run_id'), 'content_projects', ['automation_run_id'], unique=False)
    op.create_index(op.f('ix_content_projects_channel_id'), 'content_projects', ['channel_id'], unique=False)
    op.create_index(op.f('ix_content_projects_status'), 'content_projects', ['status'], unique=False)
    op.create_index(op.f('ix_content_projects_topic_candidate_id'), 'content_projects', ['topic_candidate_id'], unique=False)
    op.create_table('automation_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=True),
    sa.Column('mode', sa.String(length=32), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('triggered_by', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('current_stage', sa.String(length=48), nullable=True),
    sa.Column('stages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('stopped_reason', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_automation_runs_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_automation_runs_content_project_id_content_projects'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_automation_runs_triggered_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_runs'))
    )
    op.create_index(op.f('ix_automation_runs_channel_id'), 'automation_runs', ['channel_id'], unique=False)
    op.create_index(op.f('ix_automation_runs_content_project_id'), 'automation_runs', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_automation_runs_status'), 'automation_runs', ['status'], unique=False)
    op.create_table('content_scripts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_content_scripts_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_scripts')),
    sa.UniqueConstraint('content_project_id', name=op.f('uq_content_scripts_content_project_id'))
    )
    op.create_table('copyright_checks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('unknown_license_count', sa.Integer(), nullable=False),
    sa.Column('prohibited_count', sa.Integer(), nullable=False),
    sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_copyright_checks_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_copyright_checks'))
    )
    op.create_index(op.f('ix_copyright_checks_content_project_id'), 'copyright_checks', ['content_project_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('queue', sa.String(length=32), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=True),
    sa.Column('content_project_id', sa.UUID(), nullable=True),
    sa.Column('automation_run_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=True),
    sa.Column('worker_id', sa.String(length=96), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('permanent_failure', sa.Boolean(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_jobs_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_jobs_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_jobs_idempotency_key'))
    )
    op.create_index(op.f('ix_jobs_automation_run_id'), 'jobs', ['automation_run_id'], unique=False)
    op.create_index(op.f('ix_jobs_available_at'), 'jobs', ['available_at'], unique=False)
    op.create_index(op.f('ix_jobs_channel_id'), 'jobs', ['channel_id'], unique=False)
    op.create_index('ix_jobs_claimable', 'jobs', ['status', 'available_at', 'priority'], unique=False)
    op.create_index(op.f('ix_jobs_content_project_id'), 'jobs', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_jobs_queue'), 'jobs', ['queue'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_index(op.f('ix_jobs_type'), 'jobs', ['type'], unique=False)
    op.create_table('metadata_versions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('category_id', sa.String(length=16), nullable=True),
    sa.Column('default_language', sa.String(length=16), nullable=False),
    sa.Column('made_for_kids', sa.Boolean(), nullable=False),
    sa.Column('provider', sa.String(length=48), nullable=True),
    sa.Column('model', sa.String(length=96), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_metadata_versions_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_metadata_versions')),
    sa.UniqueConstraint('content_project_id', 'version', name='uq_metadata_versions_project_version')
    )
    op.create_index(op.f('ix_metadata_versions_content_project_id'), 'metadata_versions', ['content_project_id'], unique=False)
    op.create_table('publish_jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('privacy_status', sa.String(length=16), nullable=False),
    sa.Column('publish_at_youtube', sa.DateTime(timezone=True), nullable=True),
    sa.Column('authorized_by', sa.String(length=16), nullable=True),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('permanent_failure', sa.Boolean(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('youtube_video_id', sa.String(length=64), nullable=True),
    sa.Column('upload_bytes', sa.BigInteger(), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('preflight', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_publish_jobs_approved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_publish_jobs_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_publish_jobs_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_publish_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_publish_jobs_idempotency_key'))
    )
    op.create_index(op.f('ix_publish_jobs_channel_id'), 'publish_jobs', ['channel_id'], unique=False)
    op.create_index(op.f('ix_publish_jobs_content_project_id'), 'publish_jobs', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_publish_jobs_job_id'), 'publish_jobs', ['job_id'], unique=False)
    op.create_index(op.f('ix_publish_jobs_scheduled_for'), 'publish_jobs', ['scheduled_for'], unique=False)
    op.create_index(op.f('ix_publish_jobs_status'), 'publish_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_publish_jobs_youtube_video_id'), 'publish_jobs', ['youtube_video_id'], unique=False)
    op.create_table('quality_checks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_quality_checks_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quality_checks'))
    )
    op.create_index(op.f('ix_quality_checks_content_project_id'), 'quality_checks', ['content_project_id'], unique=False)
    op.create_table('video_assets',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=48), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('source_provider', sa.String(length=64), nullable=True),
    sa.Column('license_type', sa.String(length=96), nullable=True),
    sa.Column('license_url', sa.Text(), nullable=True),
    sa.Column('license_status', sa.String(length=32), nullable=False),
    sa.Column('attribution', sa.Text(), nullable=True),
    sa.Column('usage_permission_note', sa.Text(), nullable=True),
    sa.Column('acquired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('storage_backend', sa.String(length=16), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_video_assets_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_video_assets_content_project_id_content_projects'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_video_assets'))
    )
    op.create_index(op.f('ix_video_assets_channel_id'), 'video_assets', ['channel_id'], unique=False)
    op.create_index(op.f('ix_video_assets_checksum_sha256'), 'video_assets', ['checksum_sha256'], unique=False)
    op.create_index(op.f('ix_video_assets_content_project_id'), 'video_assets', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_video_assets_kind'), 'video_assets', ['kind'], unique=False)
    op.create_index(op.f('ix_video_assets_license_status'), 'video_assets', ['license_status'], unique=False)
    op.create_table('youtube_videos',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=True),
    sa.Column('youtube_video_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('privacy_status', sa.String(length=16), nullable=True),
    sa.Column('upload_status', sa.String(length=32), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('thumbnail_url', sa.Text(), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_youtube_videos_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_youtube_videos_content_project_id_content_projects'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_youtube_videos')),
    sa.UniqueConstraint('channel_id', 'youtube_video_id', name='uq_youtube_videos_channel_video')
    )
    op.create_index(op.f('ix_youtube_videos_channel_id'), 'youtube_videos', ['channel_id'], unique=False)
    op.create_index(op.f('ix_youtube_videos_content_project_id'), 'youtube_videos', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_youtube_videos_published_at'), 'youtube_videos', ['published_at'], unique=False)
    op.create_table('analytics_snapshots',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('video_id', sa.UUID(), nullable=True),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=48), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('unavailable_metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_complete', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_analytics_snapshots_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['video_id'], ['youtube_videos.id'], name=op.f('fk_analytics_snapshots_video_id_youtube_videos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_analytics_snapshots'))
    )
    op.create_index(op.f('ix_analytics_snapshots_captured_at'), 'analytics_snapshots', ['captured_at'], unique=False)
    op.create_index(op.f('ix_analytics_snapshots_channel_id'), 'analytics_snapshots', ['channel_id'], unique=False)
    op.create_index('ix_analytics_snapshots_scope_captured', 'analytics_snapshots', ['channel_id', 'scope', 'captured_at'], unique=False)
    op.create_index(op.f('ix_analytics_snapshots_video_id'), 'analytics_snapshots', ['video_id'], unique=False)
    op.create_table('content_performance_features',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('video_id', sa.UUID(), nullable=False),
    sa.Column('topic_category', sa.String(length=64), nullable=True),
    sa.Column('video_format', sa.String(length=32), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('published_hour_local', sa.Integer(), nullable=True),
    sa.Column('published_weekday', sa.Integer(), nullable=True),
    sa.Column('title_length', sa.Integer(), nullable=True),
    sa.Column('title_has_number', sa.Boolean(), nullable=True),
    sa.Column('title_has_question', sa.Boolean(), nullable=True),
    sa.Column('thumbnail_generator', sa.String(length=48), nullable=True),
    sa.Column('views', sa.BigInteger(), nullable=True),
    sa.Column('likes', sa.BigInteger(), nullable=True),
    sa.Column('comments', sa.BigInteger(), nullable=True),
    sa.Column('impressions', sa.BigInteger(), nullable=True),
    sa.Column('ctr_percent', sa.Float(), nullable=True),
    sa.Column('average_view_duration_seconds', sa.Float(), nullable=True),
    sa.Column('average_view_percentage', sa.Float(), nullable=True),
    sa.Column('measured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_content_performance_features_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['video_id'], ['youtube_videos.id'], name=op.f('fk_content_performance_features_video_id_youtube_videos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_performance_features')),
    sa.UniqueConstraint('video_id', name=op.f('uq_content_performance_features_video_id'))
    )
    op.create_index(op.f('ix_content_performance_features_channel_id'), 'content_performance_features', ['channel_id'], unique=False)
    op.create_index(op.f('ix_content_performance_features_topic_category'), 'content_performance_features', ['topic_category'], unique=False)
    op.create_table('job_logs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_logs_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_logs'))
    )
    op.create_index(op.f('ix_job_logs_created_at'), 'job_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_job_logs_job_id'), 'job_logs', ['job_id'], unique=False)
    op.create_table('script_versions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('script_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('sections', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('plain_text', sa.Text(), nullable=False),
    sa.Column('narration_text', sa.Text(), nullable=False),
    sa.Column('source_references', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('estimated_duration_seconds', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=48), nullable=True),
    sa.Column('model', sa.String(length=96), nullable=True),
    sa.Column('prompt_digest', sa.String(length=64), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_script_versions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['script_id'], ['content_scripts.id'], name=op.f('fk_script_versions_script_id_content_scripts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_script_versions')),
    sa.UniqueConstraint('script_id', 'version', name='uq_script_versions_script_version')
    )
    op.create_index(op.f('ix_script_versions_script_id'), 'script_versions', ['script_id'], unique=False)
    op.create_table('thumbnails',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('asset_id', sa.UUID(), nullable=True),
    sa.Column('generator', sa.String(length=48), nullable=False),
    sa.Column('concept', sa.Text(), nullable=True),
    sa.Column('headline', sa.String(length=120), nullable=True),
    sa.Column('prompt', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['video_assets.id'], name=op.f('fk_thumbnails_asset_id_video_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_thumbnails_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_thumbnails_decided_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thumbnails'))
    )
    op.create_index(op.f('ix_thumbnails_content_project_id'), 'thumbnails', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_thumbnails_status'), 'thumbnails', ['status'], unique=False)
    op.create_table('video_projects',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('aspect_ratio', sa.String(length=16), nullable=False),
    sa.Column('resolution', sa.String(length=16), nullable=False),
    sa.Column('template', sa.String(length=64), nullable=False),
    sa.Column('scene_plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('subtitle_burn_in', sa.Boolean(), nullable=False),
    sa.Column('music_asset_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_video_projects_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['music_asset_id'], ['video_assets.id'], name=op.f('fk_video_projects_music_asset_id_video_assets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_video_projects')),
    sa.UniqueConstraint('content_project_id', name=op.f('uq_video_projects_content_project_id'))
    )
    op.create_table('fact_checks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('script_version_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('supported_count', sa.Integer(), nullable=False),
    sa.Column('needs_review_count', sa.Integer(), nullable=False),
    sa.Column('unsupported_count', sa.Integer(), nullable=False),
    sa.Column('claims', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('contradictions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('provider', sa.String(length=48), nullable=True),
    sa.Column('model', sa.String(length=96), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_fact_checks_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['script_version_id'], ['script_versions.id'], name=op.f('fk_fact_checks_script_version_id_script_versions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fact_checks'))
    )
    op.create_index(op.f('ix_fact_checks_content_project_id'), 'fact_checks', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_fact_checks_status'), 'fact_checks', ['status'], unique=False)
    op.create_table('performance_observations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('video_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=True),
    sa.Column('signal', sa.String(length=64), nullable=False),
    sa.Column('observation', sa.Text(), nullable=False),
    sa.Column('possible_factors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('suggested_investigation', sa.Text(), nullable=True),
    sa.Column('measured', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_performance_observations_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['analytics_snapshots.id'], name=op.f('fk_performance_observations_snapshot_id_analytics_snapshots'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['video_id'], ['youtube_videos.id'], name=op.f('fk_performance_observations_video_id_youtube_videos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_performance_observations'))
    )
    op.create_index(op.f('ix_performance_observations_channel_id'), 'performance_observations', ['channel_id'], unique=False)
    op.create_index(op.f('ix_performance_observations_video_id'), 'performance_observations', ['video_id'], unique=False)
    op.create_table('video_render_jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('video_project_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('progress_percent', sa.Integer(), nullable=False),
    sa.Column('output_asset_id', sa.UUID(), nullable=True),
    sa.Column('subtitle_asset_id', sa.UUID(), nullable=True),
    sa.Column('ffmpeg_version', sa.String(length=128), nullable=True),
    sa.Column('command_digest', sa.String(length=64), nullable=True),
    sa.Column('log_excerpt', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['output_asset_id'], ['video_assets.id'], name=op.f('fk_video_render_jobs_output_asset_id_video_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['subtitle_asset_id'], ['video_assets.id'], name=op.f('fk_video_render_jobs_subtitle_asset_id_video_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['video_project_id'], ['video_projects.id'], name=op.f('fk_video_render_jobs_video_project_id_video_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_video_render_jobs'))
    )
    op.create_index(op.f('ix_video_render_jobs_job_id'), 'video_render_jobs', ['job_id'], unique=False)
    op.create_index(op.f('ix_video_render_jobs_video_project_id'), 'video_render_jobs', ['video_project_id'], unique=False)
    op.create_table('voice_jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('content_project_id', sa.UUID(), nullable=False),
    sa.Column('script_version_id', sa.UUID(), nullable=True),
    sa.Column('job_id', sa.Uuid(), nullable=True),
    sa.Column('provider', sa.String(length=48), nullable=True),
    sa.Column('voice_id', sa.String(length=128), nullable=True),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('audio_asset_id', sa.UUID(), nullable=True),
    sa.Column('character_count', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['audio_asset_id'], ['video_assets.id'], name=op.f('fk_voice_jobs_audio_asset_id_video_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['content_project_id'], ['content_projects.id'], name=op.f('fk_voice_jobs_content_project_id_content_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['script_version_id'], ['script_versions.id'], name=op.f('fk_voice_jobs_script_version_id_script_versions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_voice_jobs'))
    )
    op.create_index(op.f('ix_voice_jobs_content_project_id'), 'voice_jobs', ['content_project_id'], unique=False)
    op.create_index(op.f('ix_voice_jobs_job_id'), 'voice_jobs', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_voice_jobs_job_id'), table_name='voice_jobs')
    op.drop_index(op.f('ix_voice_jobs_content_project_id'), table_name='voice_jobs')
    op.drop_table('voice_jobs')
    op.drop_index(op.f('ix_video_render_jobs_video_project_id'), table_name='video_render_jobs')
    op.drop_index(op.f('ix_video_render_jobs_job_id'), table_name='video_render_jobs')
    op.drop_table('video_render_jobs')
    op.drop_index(op.f('ix_performance_observations_video_id'), table_name='performance_observations')
    op.drop_index(op.f('ix_performance_observations_channel_id'), table_name='performance_observations')
    op.drop_table('performance_observations')
    op.drop_index(op.f('ix_fact_checks_status'), table_name='fact_checks')
    op.drop_index(op.f('ix_fact_checks_content_project_id'), table_name='fact_checks')
    op.drop_table('fact_checks')
    op.drop_table('video_projects')
    op.drop_index(op.f('ix_thumbnails_status'), table_name='thumbnails')
    op.drop_index(op.f('ix_thumbnails_content_project_id'), table_name='thumbnails')
    op.drop_table('thumbnails')
    op.drop_index(op.f('ix_script_versions_script_id'), table_name='script_versions')
    op.drop_table('script_versions')
    op.drop_index(op.f('ix_job_logs_job_id'), table_name='job_logs')
    op.drop_index(op.f('ix_job_logs_created_at'), table_name='job_logs')
    op.drop_table('job_logs')
    op.drop_index(op.f('ix_content_performance_features_topic_category'), table_name='content_performance_features')
    op.drop_index(op.f('ix_content_performance_features_channel_id'), table_name='content_performance_features')
    op.drop_table('content_performance_features')
    op.drop_index(op.f('ix_analytics_snapshots_video_id'), table_name='analytics_snapshots')
    op.drop_index('ix_analytics_snapshots_scope_captured', table_name='analytics_snapshots')
    op.drop_index(op.f('ix_analytics_snapshots_channel_id'), table_name='analytics_snapshots')
    op.drop_index(op.f('ix_analytics_snapshots_captured_at'), table_name='analytics_snapshots')
    op.drop_table('analytics_snapshots')
    op.drop_index(op.f('ix_youtube_videos_published_at'), table_name='youtube_videos')
    op.drop_index(op.f('ix_youtube_videos_content_project_id'), table_name='youtube_videos')
    op.drop_index(op.f('ix_youtube_videos_channel_id'), table_name='youtube_videos')
    op.drop_table('youtube_videos')
    op.drop_index(op.f('ix_video_assets_license_status'), table_name='video_assets')
    op.drop_index(op.f('ix_video_assets_kind'), table_name='video_assets')
    op.drop_index(op.f('ix_video_assets_content_project_id'), table_name='video_assets')
    op.drop_index(op.f('ix_video_assets_checksum_sha256'), table_name='video_assets')
    op.drop_index(op.f('ix_video_assets_channel_id'), table_name='video_assets')
    op.drop_table('video_assets')
    op.drop_index(op.f('ix_quality_checks_content_project_id'), table_name='quality_checks')
    op.drop_table('quality_checks')
    op.drop_index(op.f('ix_publish_jobs_youtube_video_id'), table_name='publish_jobs')
    op.drop_index(op.f('ix_publish_jobs_status'), table_name='publish_jobs')
    op.drop_index(op.f('ix_publish_jobs_scheduled_for'), table_name='publish_jobs')
    op.drop_index(op.f('ix_publish_jobs_job_id'), table_name='publish_jobs')
    op.drop_index(op.f('ix_publish_jobs_content_project_id'), table_name='publish_jobs')
    op.drop_index(op.f('ix_publish_jobs_channel_id'), table_name='publish_jobs')
    op.drop_table('publish_jobs')
    op.drop_index(op.f('ix_metadata_versions_content_project_id'), table_name='metadata_versions')
    op.drop_table('metadata_versions')
    op.drop_index(op.f('ix_jobs_type'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_queue'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_content_project_id'), table_name='jobs')
    op.drop_index('ix_jobs_claimable', table_name='jobs')
    op.drop_index(op.f('ix_jobs_channel_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_available_at'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_automation_run_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_copyright_checks_content_project_id'), table_name='copyright_checks')
    op.drop_table('copyright_checks')
    op.drop_table('content_scripts')
    op.drop_index(op.f('ix_automation_runs_status'), table_name='automation_runs')
    op.drop_index(op.f('ix_automation_runs_content_project_id'), table_name='automation_runs')
    op.drop_index(op.f('ix_automation_runs_channel_id'), table_name='automation_runs')
    op.drop_table('automation_runs')
    op.drop_index(op.f('ix_content_projects_topic_candidate_id'), table_name='content_projects')
    op.drop_index(op.f('ix_content_projects_status'), table_name='content_projects')
    op.drop_index(op.f('ix_content_projects_channel_id'), table_name='content_projects')
    op.drop_index(op.f('ix_content_projects_automation_run_id'), table_name='content_projects')
    op.drop_table('content_projects')
    op.drop_index(op.f('ix_topic_research_topic_candidate_id'), table_name='topic_research')
    op.drop_index(op.f('ix_topic_research_channel_id'), table_name='topic_research')
    op.drop_table('topic_research')
    op.drop_index(op.f('ix_topic_candidates_trending_topic_id'), table_name='topic_candidates')
    op.drop_index(op.f('ix_topic_candidates_status'), table_name='topic_candidates')
    op.drop_index(op.f('ix_topic_candidates_opportunity_score'), table_name='topic_candidates')
    op.drop_index(op.f('ix_topic_candidates_generation_run_id'), table_name='topic_candidates')
    op.drop_index(op.f('ix_topic_candidates_channel_id'), table_name='topic_candidates')
    op.drop_table('topic_candidates')
    op.drop_index(op.f('ix_trending_topics_source_id'), table_name='trending_topics')
    op.drop_index(op.f('ix_trending_topics_published_at'), table_name='trending_topics')
    op.drop_index(op.f('ix_trending_topics_opportunity_score'), table_name='trending_topics')
    op.drop_index(op.f('ix_trending_topics_channel_id'), table_name='trending_topics')
    op.drop_index('ix_trending_topics_channel_collected', table_name='trending_topics')
    op.drop_index(op.f('ix_trending_topics_category'), table_name='trending_topics')
    op.drop_table('trending_topics')
    op.drop_index(op.f('ix_youtube_connections_youtube_channel_id'), table_name='youtube_connections')
    op.drop_table('youtube_connections')
    op.drop_index(op.f('ix_trend_sources_channel_id'), table_name='trend_sources')
    op.drop_table('trend_sources')
    op.drop_index(op.f('ix_oauth_states_state_fingerprint'), table_name='oauth_states')
    op.drop_index(op.f('ix_oauth_states_expires_at'), table_name='oauth_states')
    op.drop_table('oauth_states')
    op.drop_table('channel_settings')
    op.drop_table('automation_settings')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_entity_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_channel_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('system_settings')
    op.drop_index(op.f('ix_channels_user_id'), table_name='channels')
    op.drop_index(op.f('ix_channels_slug'), table_name='channels')
    op.drop_table('channels')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_token_fingerprint'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_expires_at'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
