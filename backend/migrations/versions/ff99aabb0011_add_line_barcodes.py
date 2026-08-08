"""add delivery_line_barcodes — several barcodes per line

Revision ID: ff99aabb0011
Revises: ee8899ffaa00
Create Date: 2026-08-08 07:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ff99aabb0011'
down_revision: Union[str, Sequence[str], None] = 'ee8899ffaa00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A product carries more than one barcode more often than not — a repackaged EAN, a second
    supplier's sticker, the manufacturer's original. Barsy models that, and so does our own
    catalogue snapshot (code → [eans], a list). Only the delivery LINE was stuck at one, so a
    product created at intake could be given exactly one code and never its others.

    A table rather than a JSON column because this is QUERIED: 'which line does this beep belong to'
    is the scanner's entire job, and a blob would turn it into a scan of every line.

    `delivery_lines.barcode` is deliberately left in place, holding the FIRST code, so every
    existing query, export and in-flight delivery keeps working untouched.

    The unique constraint is per (line, barcode): the same code twice on ONE line is a mis-tap, the
    same code on two different lines is two lots of one article and stays allowed."""
    op.create_table(
        "delivery_line_barcodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("line_id", "barcode", name="uq_line_barcode"),
    )
    op.create_index("ix_delivery_line_barcodes_line_id", "delivery_line_barcodes", ["line_id"])
    # the scanner's lookup: code -> which line
    op.create_index("ix_delivery_line_barcodes_barcode", "delivery_line_barcodes", ["barcode"])

    # backfill: every line that already has a barcode gets it as its first row, so the new table is
    # complete from the moment it exists rather than only for lines touched afterwards
    op.execute(
        "INSERT INTO delivery_line_barcodes (line_id, barcode, created_by) "
        "SELECT id, barcode, 'migration' FROM delivery_lines "
        "WHERE barcode IS NOT NULL AND TRIM(barcode) != ''"
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_line_barcodes_barcode", table_name="delivery_line_barcodes")
    op.drop_index("ix_delivery_line_barcodes_line_id", table_name="delivery_line_barcodes")
    op.drop_table("delivery_line_barcodes")
