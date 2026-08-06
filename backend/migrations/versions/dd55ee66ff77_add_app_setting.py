"""add app_setting (owner-tunable scalars)

Revision ID: dd55ee66ff77
Revises: cc44dd55ee66
Create Date: 2026-08-06 22:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'dd55ee66ff77'
down_revision: Union[str, Sequence[str], None] = 'cc44dd55ee66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A narrow key/value store for owner-tunable scalars. Today it holds exactly one key,
    shipping.free_over_eur, which used to be a module constant — so changing the free-shipping
    threshold needed a deploy.

    Additive and empty on creation: every reader falls back to its own code default when the
    key is absent, so applying this changes no behaviour until somebody edits the value."""
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
