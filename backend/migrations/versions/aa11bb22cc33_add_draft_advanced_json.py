"""add intake_drafts.advanced_json

Revision ID: aa11bb22cc33
Revises: a3b4c5d6e7f8
Create Date: 2026-08-05 08:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa11bb22cc33'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Разширени настройки captured at create time (#70 follow-up) — JSON blob
    validated as intake.AdvancedIn; merged into the Barsy article on push."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('advanced_json', sa.Text(), nullable=False,
                                      server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('intake_drafts', schema=None) as batch_op:
        batch_op.drop_column('advanced_json')
