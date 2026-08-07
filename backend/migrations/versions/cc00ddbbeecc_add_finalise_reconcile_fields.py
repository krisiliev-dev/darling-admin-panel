"""add delivery_batches.doc_num + finalise_started_at (uncertain-write reconcile)

Revision ID: cc00ddbbeecc
Revises: bb99ccaaddbb
Create Date: 2026-08-07 18:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cc00ddbbeecc'
down_revision: Union[str, Sequence[str], None] = 'bb99ccaaddbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A finalise whose Barsy response is lost leaves the batch 'finalising' with no way to tell
    whether the goods were booked. Persisting the deterministic doc_num BEFORE the write (plus when
    it started) lets an owner-only /reconcile probe Barsy for that number and either adopt the
    storeload or release the claim — instead of guessing and risking a double goods-in."""
    op.add_column("delivery_batches",
                  sa.Column("doc_num", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("delivery_batches",
                  sa.Column("finalise_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_batches", "finalise_started_at")
    op.drop_column("delivery_batches", "doc_num")
