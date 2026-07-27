"""drop online_price (reverted — channel pricing moves to Barsy native per-till)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-27 13:00:00.000000

Forward-only reversal: a1b2c3d4e5f6 (add online_price) may already be applied on
prod, so we drop the column with a new migration rather than deleting the add —
this keeps the alembic chain consistent regardless of prod's current revision.
The channel-price overlay was the wrong approach; Barsy has native per-till
pricing (the claude/API virtual till). See admin-panel/CHANNEL_PRICING.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.drop_column('online_price')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('online_price', sa.String(length=32),
                                      nullable=False, server_default=''))
