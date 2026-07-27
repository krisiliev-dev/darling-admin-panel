"""add checkout table (live/abandoned Shopify carts)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-27 18:00:00.000000

Read-only panel mirror of Shopify checkouts (checkouts/create + checkouts/update
webhooks) so the panel shows active + abandoned carts. Never touches Barsy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'checkout',
        sa.Column('token', sa.String(length=80), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('phone', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('name', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('total_price', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('currency', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('item_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('token'),
    )


def downgrade() -> None:
    op.drop_table('checkout')
