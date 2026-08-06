"""add user totp 2fa columns

Revision ID: cc44dd55ee66
Revises: bb22cc33dd44
Create Date: 2026-08-05 23:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cc44dd55ee66'
down_revision: Union[str, Sequence[str], None] = 'bb22cc33dd44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """TOTP two-factor per user (owner decision 2026-08-05: mandatory every role). Additive only —
    all default to disabled/NULL, so no existing session or login is affected by this migration."""
    op.add_column('user', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('user', sa.Column('totp_secret', sa.String(length=64), nullable=True))
    op.add_column('user', sa.Column('totp_pending_secret', sa.String(length=64), nullable=True))
    op.add_column('user', sa.Column('totp_recovery', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user', 'totp_recovery')
    op.drop_column('user', 'totp_pending_secret')
    op.drop_column('user', 'totp_secret')
    op.drop_column('user', 'totp_enabled')
