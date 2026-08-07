"""add delivery_lines.short_qty + delivery_batches.shortfall_reason (partial deliveries)

Revision ID: ff3344aabb55
Revises: ee22ff3344aa
Create Date: 2026-08-07 21:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ff3344aabb55'
down_revision: Union[str, Sequence[str], None] = 'ee22ff3344aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A delivery that arrives short now books only what arrived, so the difference has to live
    somewhere: short_qty freezes it per line at finalise (the counts stay editable, the record of
    what was billed-but-absent must not), and shortfall_reason holds the operator's explanation,
    which is required before a short finalise is allowed through."""
    op.add_column("delivery_lines",
                  sa.Column("short_qty", sa.Float(), nullable=False, server_default="0"))
    op.add_column("delivery_batches",
                  sa.Column("shortfall_reason", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("delivery_batches", "shortfall_reason")
    op.drop_column("delivery_lines", "short_qty")
