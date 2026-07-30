"""add users.is_sysadmin — the system-access capability

Owner decision 2026-07-30: system-level surfaces (Настройки/write gates, integrations + webhook config,
audit log, logs, GDPR tools, user management) move behind owner+sysadmin, while Barsy managers keep every
BUSINESS function. This deliberately narrows the 2026-07-20 decision that managers hold full write.

Why a column and not a 4th `role` value: `role` and `is_superuser` are overwritten from the Barsy role map
on every Barsy login, so a sysadmin stored in `role` would be wiped on that user's next sign-in.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 0 so existing rows are non-sysadmin: nobody GAINS system access from this
    # migration. The owner (role='owner') keeps it via the guard, so the box is never locked out.
    op.add_column("users", sa.Column("is_sysadmin", sa.Boolean(), nullable=False,
                                     server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("users", "is_sysadmin")
