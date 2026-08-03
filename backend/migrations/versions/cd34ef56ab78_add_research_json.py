"""add intake_drafts.research_json

Revision ID: cd34ef56ab78
Revises: bc23de45fa67
Create Date: 2026-08-03 20:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'cd34ef56ab78'
down_revision: Union[str, Sequence[str], None] = 'bc23de45fa67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: what the enrichment agents found (notes/sources/facts), shown to the
    human in the editor's info panel so sources can be verified."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('research_json', sa.Text(), nullable=False,
                                      server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.drop_column('research_json')
