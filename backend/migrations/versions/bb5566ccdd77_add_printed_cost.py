"""add printed invoice values to delivery lines + batches (cost as printed)

Revision ID: bb5566ccdd77
Revises: aa4455bbcc66
Create Date: 2026-08-08 02:55:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bb5566ccdd77'
down_revision: Union[str, Sequence[str], None] = 'aa4455bbcc66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A single collapsed `price` cannot be checked later. These keep the invoice line as PRINTED —
    the price, whether it already contains VAT, the ДДС group, and both discount levels — so the net
    cost is derived rather than assumed.

    `delivery_lines.price` is deliberately left alone: a line with no printed_price keeps using it
    exactly as before, so a delivery already in flight cannot change value under the operator.

    price_includes_vat defaults TRUE because that is what the panel already assumed
    (barsy_price_mode = 1 declares our prices VAT-inclusive), so existing rows keep their meaning."""
    op.add_column("delivery_lines",
                  sa.Column("printed_price", sa.Float(), nullable=False, server_default="0"))
    op.add_column("delivery_lines",
                  sa.Column("price_includes_vat", sa.Boolean(), nullable=False,
                            server_default=sa.true()))
    op.add_column("delivery_lines",
                  sa.Column("vat_group_id", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("delivery_lines",
                  sa.Column("line_discount_pct", sa.Float(), nullable=False, server_default="0"))

    op.add_column("delivery_batches",
                  sa.Column("doc_discount_pct", sa.Float(), nullable=False, server_default="0"))
    op.add_column("delivery_batches",
                  sa.Column("currency", sa.String(length=8), nullable=False, server_default="EUR"))
    op.add_column("delivery_batches",
                  sa.Column("fx_rate", sa.Float(), nullable=False, server_default="1"))
    op.add_column("delivery_batches",
                  sa.Column("invoice_total", sa.Float(), nullable=False, server_default="0"))
    op.add_column("delivery_batches",
                  sa.Column("rounding_residual", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    for col in ("rounding_residual", "invoice_total", "fx_rate", "currency", "doc_discount_pct"):
        op.drop_column("delivery_batches", col)
    for col in ("line_discount_pct", "vat_group_id", "price_includes_vat", "printed_price"):
        op.drop_column("delivery_lines", col)
