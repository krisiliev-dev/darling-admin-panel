"""add catalog_sync_state (freshness of the local product mirror)

Revision ID: e1f2a3b4c5d6
Revises: cd34ef56ab78
Create Date: 2026-08-03 12:00:00.000000

One row (id=1) recording when the ProductCache mirror was last fully reconciled against Barsy,
what that pass saw, and when a webhook last wrote through. Lets the catalogue tab show staleness
instead of silently displaying a months-old snapshot as if it were live.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'cd34ef56ab78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'catalog_sync_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('last_sync', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_attempt', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('live_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pruned', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_event_kind', sa.String(length=32), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('catalog_sync_state')
