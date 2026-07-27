"""add online_price table (channel pricing — native Barsy rule source-of-truth)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-27 17:00:00.000000

Panel-owned intended online price per article; pushed into a native Barsy price
rule scoped to the online delivery types. NOT the reverted extra_data overlay —
this is the editable source-of-truth, like pricing_rule. See CHANNEL_PRICING.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'online_price',
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('custom_price', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('mode', sa.String(length=16), nullable=False, server_default='exact'),
        sa.Column('updated_by', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('updated_ts', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('article_id'),
    )


def downgrade() -> None:
    op.drop_table('online_price')
