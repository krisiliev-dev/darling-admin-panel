"""add col_discount + price_includes_vat to the supplier import profile

Revision ID: cc6677ddee88
Revises: bb5566ccdd77
Create Date: 2026-08-08 03:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cc6677ddee88'
down_revision: Union[str, Sequence[str], None] = 'bb5566ccdd77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The printed values have to come from somewhere, and the somewhere is the supplier's sheet.
    A discount column joins the remembered layout, and whether that supplier prints prices with VAT
    already in them becomes a property of their profile rather than a per-delivery guess — it is a
    fact about their paperwork that does not change between deliveries.

    price_includes_vat defaults TRUE because that is what the panel already assumed
    (barsy_price_mode = 1), so no existing supplier changes meaning."""
    op.add_column("supplier_import_profile",
                  sa.Column("col_discount", sa.Integer(), nullable=False, server_default="-1"))
    op.add_column("supplier_import_profile",
                  sa.Column("price_includes_vat", sa.Boolean(), nullable=False,
                            server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("supplier_import_profile", "price_includes_vat")
    op.drop_column("supplier_import_profile", "col_discount")
