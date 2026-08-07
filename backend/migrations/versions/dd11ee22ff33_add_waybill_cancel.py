"""add waybill_cancel

Revision ID: dd11ee22ff33
Revises: cc00ddbbeecc
Create Date: 2026-08-07 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'dd11ee22ff33'
down_revision: Union[str, Sequence[str], None] = 'cc00ddbbeecc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One row per waybill we have tried to cancel, keyed on the waybill number so the claim is
    the INSERT itself rather than a check followed by an act.

    Additive and empty on creation. Nothing reads it until somebody cancels something, so
    applying this changes no behaviour.
    """
    op.create_table(
        "waybill_cancel",
        sa.Column("waybill_no", sa.String(length=64), primary_key=True),
        sa.Column("courier", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("order_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("order_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("courier_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("last_tried", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The worklist query is "everything not yet resolved, oldest first" - it runs on every health
    # check, so it gets an index rather than a table scan that grows with the order book.
    op.create_index("ix_waybill_cancel_unresolved", "waybill_cancel",
                    ["resolved_at", "first_seen"])


def downgrade() -> None:
    op.drop_index("ix_waybill_cancel_unresolved", table_name="waybill_cancel")
    op.drop_table("waybill_cancel")
