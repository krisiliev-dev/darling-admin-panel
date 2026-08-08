"""add delivery_invoices — document type + the N:M delivery/invoice link

Revision ID: dd7788eeff99
Revises: cc6677ddee88
Create Date: 2026-08-08 05:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'dd7788eeff99'
down_revision: Union[str, Sequence[str], None] = 'cc6677ddee88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A join table rather than an invoice column on the batch, because the owner confirmed the
    relationship is many-to-many: one invoice may cover several deliveries and one delivery may be
    split across two. A nullable column would have to be migrated across live goods-in records the
    first time either happened.

    It also separates the GOODS event from the MONEY event — stock received on a стокова разписка
    today, the фактура attached next week — which is how Barsy models it. doc_type_id holds Barsy's
    own Тип документ ids verbatim (Фактура 1, Фактура ЧВ 11, Стокова р/ка 2, Дебитно известие 5,
    Кредитно известие 4, Митническа декларация 6, Друго 3, Неизвестен -1).

    The unique constraint is per (batch, type, ref): the same document twice on ONE delivery is a
    mistake, the same document across SEVERAL deliveries is the whole point."""
    op.create_table(
        "delivery_invoices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("doc_type_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("invoice_ref", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("doc_date", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "doc_type_id", "invoice_ref",
                            name="uq_delivery_document"),
    )
    op.create_index("ix_delivery_invoices_batch_id", "delivery_invoices", ["batch_id"])
    op.create_index("ix_delivery_invoices_doc_type_id", "delivery_invoices", ["doc_type_id"])
    # the reverse lookup: which deliveries does this invoice cover?
    op.create_index("ix_delivery_invoices_invoice_ref", "delivery_invoices", ["invoice_ref"])


def downgrade() -> None:
    op.drop_index("ix_delivery_invoices_invoice_ref", table_name="delivery_invoices")
    op.drop_index("ix_delivery_invoices_doc_type_id", table_name="delivery_invoices")
    op.drop_index("ix_delivery_invoices_batch_id", table_name="delivery_invoices")
    op.drop_table("delivery_invoices")
