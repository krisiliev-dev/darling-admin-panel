"""add intake_drafts.series

Revision ID: bc23de45fa67
Revises: ab12cd34ef56
Create Date: 2026-08-03 04:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bc23de45fa67'
down_revision: Union[str, Sequence[str], None] = 'ab12cd34ef56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: product series/collection on a draft (→ Barsy „Серия" detail)."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('series', sa.String(length=128), nullable=False,
                                      server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.drop_column('series')
