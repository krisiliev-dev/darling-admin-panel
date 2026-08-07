"""add delivery_lines pack_id / pack_factor / pack_count (разфасовка)

Revision ID: aa4455bbcc66
Revises: ff3344aabb55
Create Date: 2026-08-08 01:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa4455bbcc66'
down_revision: Union[str, Sequence[str], None] = 'ff3344aabb55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A supplier billing "6 каси" delivers 72 glasses; booking 6 is the commonest silent stock
    drift in home goods. qty stays canonical in PIECES — a pack can be opened and one sold, so it
    can never be the stock unit — and these three columns record HOW that quantity was reached, so
    a wrong factor can be found and corrected rather than leaving an unexplained number.
    pack_id is Barsy's Packages id; names are not unique („Стек" is both 6 and 24)."""
    op.add_column("delivery_lines", sa.Column("pack_id", sa.Integer(), nullable=True))
    op.add_column("delivery_lines",
                  sa.Column("pack_factor", sa.Float(), nullable=False, server_default="1"))
    op.add_column("delivery_lines",
                  sa.Column("pack_count", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("delivery_lines", "pack_count")
    op.drop_column("delivery_lines", "pack_factor")
    op.drop_column("delivery_lines", "pack_id")
