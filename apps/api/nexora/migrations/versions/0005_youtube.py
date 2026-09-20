"""phase 5 youtube

Separates a publicly-verified channel id from an OAuth connection, and adds the
made-for-kids declaration, which is deliberately nullable so that "undecided"
is a distinct state from "no".

Revision ID: 81c191e651d7
Revises: 0004
Create Date: 2026-09-19 22:22:20.111108+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('channel_settings', sa.Column('made_for_kids_default', sa.Boolean(), nullable=True))
    op.add_column('channel_settings', sa.Column('youtube_category_id', sa.String(length=16), nullable=True))
    op.add_column('youtube_connections', sa.Column('public_channel_id', sa.String(length=64), nullable=True))
    op.add_column('youtube_connections', sa.Column('public_channel_title', sa.String(length=255), nullable=True))
    op.add_column('youtube_connections', sa.Column('public_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_youtube_connections_public_channel_id'), 'youtube_connections', ['public_channel_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_youtube_connections_public_channel_id'), table_name='youtube_connections')
    op.drop_column('youtube_connections', 'public_verified_at')
    op.drop_column('youtube_connections', 'public_channel_title')
    op.drop_column('youtube_connections', 'public_channel_id')
    op.drop_column('channel_settings', 'youtube_category_id')
    op.drop_column('channel_settings', 'made_for_kids_default')
