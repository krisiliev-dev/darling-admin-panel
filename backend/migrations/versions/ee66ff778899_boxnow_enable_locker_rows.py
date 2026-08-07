"""boxnow: turn the locker rows on - the BOX NOW test order is confirmed

Revision ID: ee66ff778899
Revises: ee66ff77aa88
Create Date: 2026-08-07 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ee66ff778899'
down_revision: Union[str, Sequence[str], None] = 'ee66ff77aa88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The four boxnow locker rows were seeded enabled=0 while the contract policy was off.

    _SPEC["boxnow"]["enabled"] seeds a bracket the FIRST time it appears and never touches it
    again, so flipping the policy alone would have left the two disagreeing forever - the page
    saying "the contract says yes" beside four unticked boxes. The owner confirmed the BOX NOW
    test order on 2026-08-07, which is the decision this migration records.

    NARROW ON PURPOSE: boxnow only, and only rows still at enabled=0. It is a no-op where they
    are already on, and because a migration runs once it can never re-enable something the owner
    switches off later. The switch stays his - one untick per row on the Dostavka tab.

    Consequence worth stating rather than discovering: at 1.54 flat + 0.10 buffer = 1.64 EUR
    BoxNow undercuts every counter tariff, so cheapest-first puts it at the top of checkout. Its
    COD is card-by-link and never cash, which is a real difference in a shop where COD is 55-60%
    of orders.
    """
    op.execute("UPDATE shipping_rate SET enabled = 1 "
               "WHERE courier = 'boxnow' AND enabled = 0")


def downgrade() -> None:
    """Back to dark. This is a rollback of the revision, not the owner-facing off switch - that
    one is the Dostavka tab, and it must stay the thing people reach for."""
    op.execute("UPDATE shipping_rate SET enabled = 0 WHERE courier = 'boxnow'")