# -*- coding: utf-8 -*-
"""brand_override: add `confirmed` + `kind` (brand-scope extras for the brand detail editor).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-01

Both nullable — NULL means "not overridden", the same convention as every other column in this
table: an override says what is wrong, not the whole record.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("brand_override", sa.Column("confirmed", sa.Boolean(), nullable=True))
    op.add_column("brand_override", sa.Column("kind", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("brand_override", "kind")
    op.drop_column("brand_override", "confirmed")
