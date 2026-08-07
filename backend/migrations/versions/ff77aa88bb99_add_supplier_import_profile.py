"""add supplier_import_profile (remembered delivery-sheet layouts)

Revision ID: ff77aa88bb99
Revises: ee66ff778899
Create Date: 2026-08-07 04:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ff77aa88bb99'
down_revision: Union[str, Sequence[str], None] = 'ee66ff778899'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One remembered column layout per supplier: which column is code/name/qty/price/barcode
    (0-based; -1 = absent) and which 1-based row the products start on. Additive and empty on
    creation — with no profile the importer auto-detects exactly as before."""
    op.create_table(
        "supplier_import_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("supplier", sa.String(length=255), nullable=False),
        sa.Column("supplier_norm", sa.String(length=255), nullable=False),
        sa.Column("data_row", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("col_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("col_name", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("col_qty", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("col_price", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("col_barcode", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_supplier_import_profile_supplier", "supplier_import_profile", ["supplier"])
    op.create_index("ix_supplier_import_profile_supplier_norm", "supplier_import_profile",
                    ["supplier_norm"], unique=True)


def downgrade() -> None:
    op.drop_table("supplier_import_profile")
