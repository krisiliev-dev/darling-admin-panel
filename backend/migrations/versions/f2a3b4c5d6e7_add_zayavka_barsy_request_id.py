"""add zayavki.barsy_request_id

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-04 01:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Barsy's own request id for a заявка pushed to the ERP (Requests_save).

    NULL means panel-only — never pushed. Storing it is what makes the push idempotent: a second
    push updates that request instead of ordering the same goods twice.
    """
    with op.batch_alter_table('zayavki', schema=None) as batch_op:
        batch_op.add_column(sa.Column('barsy_request_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('zayavki', schema=None) as batch_op:
        batch_op.drop_column('barsy_request_id')
