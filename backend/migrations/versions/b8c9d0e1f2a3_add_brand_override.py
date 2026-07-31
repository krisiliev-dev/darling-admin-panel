"""add brand_override — human corrections to the machine-derived brand registry (#99)

The registry snapshot is regenerated freely by the pipeline and is read-only in the panel. Corrections
live here and win at resolution time, so a regeneration can never discard a human judgement — the
failure already seen with gazetteer.jsonl, whose hand-edits were overwritten on the next build.

`why` and `actor` are not decoration: flipping `asserts` on one prefix can re-brand thousands of
products, and an unattributable change to that is not reviewable afterwards.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brand_override",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),      # prefix | brand
        sa.Column("ref", sa.String(length=64), nullable=False),
        # NULL means "not overridden" — an override states only what is wrong, not the whole record
        sa.Column("asserts", sa.Boolean(), nullable=True),
        sa.Column("brand_id", sa.String(length=64), nullable=True),
        sa.Column("canonical", sa.String(length=160), nullable=True),
        sa.Column("aliases_json", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), server_default="", nullable=False),
        sa.Column("actor", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # one override row per target, so corrections accumulate by field rather than by row
        sa.UniqueConstraint("scope", "ref", name="uq_brand_override_target"),
    )
    op.create_index("ix_brand_override_scope", "brand_override", ["scope"])
    op.create_index("ix_brand_override_ref", "brand_override", ["ref"])


def downgrade() -> None:
    op.drop_index("ix_brand_override_ref", table_name="brand_override")
    op.drop_index("ix_brand_override_scope", table_name="brand_override")
    op.drop_table("brand_override")
