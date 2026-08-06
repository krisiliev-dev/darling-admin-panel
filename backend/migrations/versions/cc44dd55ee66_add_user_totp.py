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

# NB the table is "users" (plural) — fastapi-users' UUID base table as this app names it; the
# first cut of this migration said "user" and CI's alembic-head-vs-models drift test caught it
# with "no such table: user". Match a7b8c9d0e1f2 (add is_sysadmin), which is the house precedent.
_T = "users"


def upgrade() -> None:
    """TOTP two-factor per user (owner decision 2026-08-05: mandatory for every role). Additive
    only — every column defaults to disabled/NULL, so applying this changes no existing login."""
    op.add_column(_T, sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column(_T, sa.Column('totp_secret', sa.String(length=64), nullable=True))
    op.add_column(_T, sa.Column('totp_pending_secret', sa.String(length=64), nullable=True))
    op.add_column(_T, sa.Column('totp_recovery', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column(_T, 'totp_recovery')
    op.drop_column(_T, 'totp_pending_secret')
    op.drop_column(_T, 'totp_secret')
    op.drop_column(_T, 'totp_enabled')
