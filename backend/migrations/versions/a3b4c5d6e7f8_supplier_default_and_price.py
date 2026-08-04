"""#78 draft_suppliers: is_default + delivery_price

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-04 19:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """#78: the „Основен" flag + „договорена цена" on a draft's supplier rows.

    is_default lets intake pick the default supplier; delivery_price records a negotiated delivery
    price. _push_supplier_links sends both to Barsy's Articles_save_suppliers (is_default /
    default_delivery_price). delivery_price is a string — never a float on a money field.
    """
    with op.batch_alter_table('draft_suppliers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_default', sa.Boolean(), nullable=False,
                                      server_default=sa.text('0')))
        batch_op.add_column(sa.Column('delivery_price', sa.String(length=32), nullable=False,
                                      server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('draft_suppliers', schema=None) as batch_op:
        batch_op.drop_column('delivery_price')
        batch_op.drop_column('is_default')
