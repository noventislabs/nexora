"""phase 4 media production

Adds measured-duration and timing-provenance columns for narration and renders.

Revision ID: 7524fcfd2b70
Revises: 0003
Create Date: 2026-09-19 21:58:12.818640+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('thumbnails', sa.Column('width', sa.Integer(), nullable=True))
    op.add_column('thumbnails', sa.Column('height', sa.Integer(), nullable=True))
    op.add_column('video_render_jobs', sa.Column('duration_seconds', sa.Float(), nullable=True))
    op.add_column('video_render_jobs', sa.Column('resolution', sa.String(length=16), nullable=True))
    op.add_column('video_render_jobs', sa.Column('output_bytes', sa.BigInteger(), nullable=True))
    op.add_column('voice_jobs', sa.Column('timing_source', sa.String(length=16), nullable=True))
    op.add_column('voice_jobs', sa.Column('segments', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('voice_jobs', sa.Column('mime_type', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('voice_jobs', 'mime_type')
    op.drop_column('voice_jobs', 'segments')
    op.drop_column('voice_jobs', 'timing_source')
    op.drop_column('video_render_jobs', 'output_bytes')
    op.drop_column('video_render_jobs', 'resolution')
    op.drop_column('video_render_jobs', 'duration_seconds')
    op.drop_column('thumbnails', 'height')
    op.drop_column('thumbnails', 'width')
