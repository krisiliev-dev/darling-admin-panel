"""add processing_issue table (connector/webhook anomalies)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-27 21:30:00.000000

Owner 2026-07-27: surface connector refusals/failures in the panel instead of swallowing them.
One row per anomaly (unmatched SKU, missing field, unhandled topic, exception), deduped by
(source, kind, order_ref); staff acknowledge via `resolved`. Panel-owned; never touches Barsy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'processing_issue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='shopify'),
        sa.Column('topic', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('order_ref', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('severity', sa.String(length=16), nullable=False, server_default='warning'),
        sa.Column('kind', sa.String(length=48), nullable=False, server_default=''),
        sa.Column('summary', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('detail', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('resolved', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.String(length=255), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_processing_issue_created_at', 'processing_issue', ['created_at'])
    op.create_index('ix_processing_issue_order_ref', 'processing_issue', ['order_ref'])
    op.create_index('ix_processing_issue_resolved', 'processing_issue', ['resolved'])
    op.create_index('ix_issue_open', 'processing_issue', ['resolved', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_issue_open', table_name='processing_issue')
    op.drop_index('ix_processing_issue_resolved', table_name='processing_issue')
    op.drop_index('ix_processing_issue_order_ref', table_name='processing_issue')
    op.drop_index('ix_processing_issue_created_at', table_name='processing_issue')
    op.drop_table('processing_issue')
