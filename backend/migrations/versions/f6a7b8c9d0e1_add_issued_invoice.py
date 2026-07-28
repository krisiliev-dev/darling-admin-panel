"""add issued_invoice — the B2B фактура worklist's 'done' side

Barsy's API cannot issue фактури (no Invoices_create; the auto-issue flag is VPOS-only), so staff issue
them by hand in Документи → Фактури → „Нова фактура по сметка(и)". The connector knows WHICH orders asked
for one; this table records which have been ISSUED (and under what number) so a legally-owed invoice is
neither forgotten nor issued twice.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "issued_invoice",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("order_ref", sa.String(length=64), server_default="", nullable=False),
        sa.Column("account_id", sa.String(length=32), server_default="", nullable=False),
        sa.Column("invoice_number", sa.String(length=32), server_default="", nullable=False),
        sa.Column("company", sa.String(length=255), server_default="", nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("issued_by", sa.String(length=255), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),        # one фактура per order — the anti-double-issue guard
    )
    op.create_index("ix_issued_invoice_order_id", "issued_invoice", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_issued_invoice_order_id", table_name="issued_invoice")
    op.drop_table("issued_invoice")
