"""add delivery_batches.zayavka_id (receive a delivery from a Заявка)

Revision ID: aa88bb99ccaa
Revises: ff77aa88bb99
Create Date: 2026-08-07 16:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa88bb99ccaa'
down_revision: Union[str, Sequence[str], None] = 'ff77aa88bb99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Link a received delivery back to the заявка it fulfils, so finalising the delivery flips
    that заявка to 'received' (Barsy's Ново зареждане carries the same native „Заявка №" link).
    Nullable and additive — deliveries not made from a заявка simply leave it null."""
    op.add_column("delivery_batches",
                  sa.Column("zayavka_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_batches", "zayavka_id")
