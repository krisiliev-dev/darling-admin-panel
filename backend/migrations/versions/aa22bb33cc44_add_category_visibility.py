"""add category_visibility (+ seed hg-1..hg-21)

Revision ID: aa22bb33cc44
Revises: dd11ee22ff33
Create Date: 2026-08-07 21:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa22bb33cc44'
down_revision: Union[str, Sequence[str], None] = 'dd11ee22ff33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Owner decision 2026-08-07: the 21 level-2 Home & Garden categories are on, everything else off.
# Seeded here as real rows rather than kept as a list in code, so the default-on set is DATA the
# owner can see and change, and so turning one off later is an ordinary write and not a special
# case in an if-statement.
_DEFAULT_ON = ["hg-%d" % n for n in range(1, 22)]


def upgrade() -> None:
    """Panel-side visibility state. Barsy has no such field (schema captured 2026-08-07), so it
    cannot live in the ERP without overloading cat_type or code to mean something the ERP never
    intended.

    Additive. Nothing reads this table until the storefront is pointed at the enabled-ids feed,
    so applying it changes no behaviour on its own.
    """
    op.create_table(
        "category_visibility",
        sa.Column("slug", sa.String(length=128), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.bulk_insert(
        sa.table("category_visibility",
                 sa.column("slug", sa.String),
                 sa.column("enabled", sa.Boolean),
                 sa.column("updated_by", sa.String)),
        [{"slug": s, "enabled": True, "updated_by": "migration:owner-default-2026-08-07"}
         for s in _DEFAULT_ON],
    )


def downgrade() -> None:
    op.drop_table("category_visibility")
