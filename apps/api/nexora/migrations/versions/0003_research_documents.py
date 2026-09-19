"""phase 3 research documents

Adds the per-document provenance table that research runs stand on, plus the
counted (never estimated) document total on each research row.

Revision ID: be993e832510
Revises: 0002
Create Date: 2026-09-19 21:33:24.393103+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('research_documents',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('research_id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('trending_topic_id', sa.UUID(), nullable=True),
    sa.Column('origin', sa.String(length=32), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('publisher', sa.String(length=255), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('content_type', sa.String(length=128), nullable=True),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
    sa.Column('fetch_decision', sa.String(length=32), nullable=False),
    sa.Column('fetch_note', sa.Text(), nullable=True),
    sa.Column('license_note', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_research_documents_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['research_id'], ['topic_research.id'], name=op.f('fk_research_documents_research_id_topic_research'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trending_topic_id'], ['trending_topics.id'], name=op.f('fk_research_documents_trending_topic_id_trending_topics'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_documents'))
    )
    op.create_index(op.f('ix_research_documents_channel_id'), 'research_documents', ['channel_id'], unique=False)
    op.create_index(op.f('ix_research_documents_research_id'), 'research_documents', ['research_id'], unique=False)
    op.create_index(op.f('ix_research_documents_trending_topic_id'), 'research_documents', ['trending_topic_id'], unique=False)
    op.add_column(
        'topic_research',
        sa.Column('document_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('topic_research', 'document_count', server_default=None)
    op.add_column('topic_research', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'channel_settings',
        sa.Column(
            'research_full_text_enabled', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        'channel_settings',
        sa.Column('research_min_documents', sa.Integer(), nullable=False, server_default='2'),
    )
    op.alter_column('channel_settings', 'research_full_text_enabled', server_default=None)
    op.alter_column('channel_settings', 'research_min_documents', server_default=None)
    op.add_column('topic_research', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('topic_research', 'finished_at')
    op.drop_column('topic_research', 'started_at')
    op.drop_column('channel_settings', 'research_min_documents')
    op.drop_column('channel_settings', 'research_full_text_enabled')
    op.drop_column('topic_research', 'document_count')
    op.drop_index(op.f('ix_research_documents_trending_topic_id'), table_name='research_documents')
    op.drop_index(op.f('ix_research_documents_research_id'), table_name='research_documents')
    op.drop_index(op.f('ix_research_documents_channel_id'), table_name='research_documents')
    op.drop_table('research_documents')
