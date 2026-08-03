"""add draft_suppliers

Revision ID: ab12cd34ef56
Revises: c9d0e1f2a3b4
Create Date: 2026-08-03 03:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ab12cd34ef56'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: supplier rows on intake drafts (who supplies the product, under what
    code of theirs, what they call it)."""
    op.create_table(
        'draft_suppliers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('draft_id', sa.Integer(), nullable=False),
        sa.Column('supplier', sa.String(length=255), nullable=False),
        sa.Column('supplier_code', sa.String(length=128), nullable=False),
        sa.Column('supplier_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_draft_suppliers_draft_id'), 'draft_suppliers', ['draft_id'],
                    unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_draft_suppliers_draft_id'), table_name='draft_suppliers')
    op.drop_table('draft_suppliers')
