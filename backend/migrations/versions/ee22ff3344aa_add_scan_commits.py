"""add scan_commits (camera commit idempotency)

Revision ID: ee22ff3344aa
Revises: dd11ee22ff33
Create Date: 2026-08-07 20:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ee22ff3344aa'
down_revision: Union[str, Sequence[str], None] = 'aa22bb33cc44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A camera commit whose reply is lost to warehouse wifi gets retried by the worker. Without a
    key the retry books the whole box a second time. The scanner sends the same id on every retry
    of one tally; the UNIQUE index is what makes the second apply impossible — a check-then-act in
    Python would let two concurrent retries through."""
    op.create_table(
        "scan_commits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("commit_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scan_commits_batch_id", "scan_commits", ["batch_id"])
    op.create_index("ix_scan_commits_commit_id", "scan_commits", ["commit_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_scan_commits_commit_id", table_name="scan_commits")
    op.drop_index("ix_scan_commits_batch_id", table_name="scan_commits")
    op.drop_table("scan_commits")
