# -*- coding: utf-8 -*-
"""Fast nav-badge counts — pending work per module, so the nav shows where attention is
needed (operator-tool state-visibility pattern). Fast/local only: no Barsy/courier calls."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..core import db
from ..db import get_async_session
from ..models import IntakeDraft, User
from ..users import current_active_user

router = APIRouter(prefix="/api/summary", tags=["summary"])


def _fulfillment_counts() -> tuple[int, int]:
    """Sync sqlite reads — run in a threadpool from the async handler."""
    orders_todo = cod_awaiting = 0
    c = db.connect()
    try:
        orders_todo = c.execute(
            "SELECT COUNT(*) FROM fulfillment_state "
            "WHERE status IN ('new','picking','packed')").fetchone()[0] or 0
        cod_awaiting = c.execute(
            "SELECT COUNT(*) FROM fulfillment_state "
            "WHERE status='delivered' AND cod_amount>0").fetchone()[0] or 0
    except Exception:
        pass
    finally:
        c.close()
    return orders_todo, cod_awaiting


@router.get("/badges")
async def badges(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Counts of work awaiting action, keyed by nav route."""
    drafts = (await session.execute(
        select(func.count()).select_from(IntakeDraft)
        .where(IntakeDraft.status == "approved"))).scalar_one()
    orders_todo, cod_awaiting = await run_in_threadpool(_fulfillment_counts)
    # keyed by nav path so the Shell can map directly
    return {"/intake": drafts, "/orders": orders_todo, "/cod": cod_awaiting}
