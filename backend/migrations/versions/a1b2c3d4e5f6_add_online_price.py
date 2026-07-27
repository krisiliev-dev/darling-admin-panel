"""add online_price (channel/per-POS pricing)

Revision ID: a1b2c3d4e5f6
Revises: fe0dd343875f
Create Date: 2026-07-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'fe0dd343875f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default '' backfills existing rows — unlike weight_kg (adopted via stamp), this
    # migration runs against a populated intake_drafts, so a bare NOT NULL would fail the rebuild.
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('online_price', sa.String(length=32),
                                      nullable=False, server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.drop_column('online_price')
