"""add shopfloor_move (the warehouse-door kiosk ledger)

Revision ID: ee66ff77aa88
Revises: dd55ee66ff77
Create Date: 2026-08-07 02:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ee66ff77aa88'
down_revision: Union[str, Sequence[str], None] = 'dd55ee66ff77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The mounted-tablet kiosk's ledger: one row per wave past the warehouse-door scanner.
    Panel-local by design — Barsy keeps one depot and total stock; this table derives the
    warehouse/shopfloor split, so best-effort data can never gate ERP stock. Additive and
    empty on creation."""
    op.create_table(
        "shopfloor_move",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_shopfloor_move_barcode", "shopfloor_move", ["barcode"])
    op.create_index("ix_shopfloor_move_code", "shopfloor_move", ["code"])
    op.create_index("ix_shopfloor_move_direction", "shopfloor_move", ["direction"])
    op.create_index("ix_shopfloor_move_created_at", "shopfloor_move", ["created_at"])


def downgrade() -> None:
    op.drop_table("shopfloor_move")
