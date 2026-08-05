"""add shipping_rate

Revision ID: bb22cc33dd44
Revises: aa11bb22cc33
Create Date: 2026-08-05 13:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bb22cc33dd44'
down_revision: Union[str, Sequence[str], None] = 'aa11bb22cc33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Delivery-fee tariff table (courier × mode × weight bracket) — DELIVERY_FEE_DESIGN v2."""
    op.create_table(
        'shipping_rate',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('courier', sa.String(length=24), nullable=False, index=True),
        sa.Column('mode', sa.String(length=8), nullable=False),
        sa.Column('max_kg', sa.Float(), nullable=False),
        sa.Column('label_cost', sa.Float(), nullable=False, server_default='0'),
        sa.Column('cod_fee', sa.Float(), nullable=False, server_default='0'),
        sa.Column('buffer', sa.Float(), nullable=False, server_default='0'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('refreshed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('shipping_rate')
