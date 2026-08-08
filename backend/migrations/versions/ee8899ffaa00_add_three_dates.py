"""add delivery_batches arrival_date + posting_date (the three dates)

Revision ID: ee8899ffaa00
Revises: dd7788eeff99
Create Date: 2026-08-08 06:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ee8899ffaa00'
down_revision: Union[str, Sequence[str], None] = 'dd7788eeff99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A delivery has three dates that routinely differ, and collapsing them loses information that
    matters twice over.

    ЗДДС чл.113 wants the invoice issued within 5 days of the tax event, so the gap between arrival
    and the document's own date is a compliance signal available for free once both are recorded.

    And posting follows ARRIVAL rather than the moment somebody pressed Финализирай: a receipt
    backdated after the goods have already been sold permanently corrupts the moving-average
    себестойност — that error never washes out of the average.

    `doc_date` already exists on the batch and is reused as the document's own date; only arrival
    and posting are new. Both default to empty, and existing rows simply have no arrival recorded —
    which is honest, since we do not know when those goods landed."""
    op.add_column("delivery_batches",
                  sa.Column("arrival_date", sa.String(length=32), nullable=False,
                            server_default=""))
    op.add_column("delivery_batches",
                  sa.Column("posting_date", sa.String(length=32), nullable=False,
                            server_default=""))


def downgrade() -> None:
    op.drop_column("delivery_batches", "posting_date")
    op.drop_column("delivery_batches", "arrival_date")
