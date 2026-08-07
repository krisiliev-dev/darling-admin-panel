"""add delivery_lines.defect_qty + supplier_claims (defective-items flow)

Revision ID: bb99ccaaddbb
Revises: aa88bb99ccaa
Create Date: 2026-08-07 17:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bb99ccaaddbb'
down_revision: Union[str, Sequence[str], None] = 'aa88bb99ccaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Defectives are set aside, not received: a line gains defect_qty (received = qty − defect_qty,
    so the storeload books only good units), and each defect opens a supplier_claims row the
    Рекламации view tracks to resolution. Both additive; existing rows default to 0 / pending."""
    op.add_column("delivery_lines",
                  sa.Column("defect_qty", sa.Float(), nullable=False, server_default="0"))
    op.create_table(
        "supplier_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("supplier", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("supplier_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("qty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_supplier_claims_batch_id", "supplier_claims", ["batch_id"])
    op.create_index("ix_supplier_claims_supplier", "supplier_claims", ["supplier"])
    op.create_index("ix_supplier_claims_status", "supplier_claims", ["status"])


def downgrade() -> None:
    op.drop_index("ix_supplier_claims_status", table_name="supplier_claims")
    op.drop_index("ix_supplier_claims_supplier", table_name="supplier_claims")
    op.drop_index("ix_supplier_claims_batch_id", table_name="supplier_claims")
    op.drop_table("supplier_claims")
    op.drop_column("delivery_lines", "defect_qty")
