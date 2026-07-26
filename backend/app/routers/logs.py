# -*- coding: utf-8 -*-
"""Дневник — the audit trail view (owner-gated). Reads the append-only audit_log: semantic events
(action like 'intake.approve') + every mutating request (action like 'http:POST'). Filterable by
actor / action / free text / date range."""
import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..models import AuditLog, User
from ..users import current_owner

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _bound(s: str, end: bool = False) -> dt.datetime | None:
    try:
        d = dt.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time.min, tzinfo=dt.timezone.utc)


@router.get("")
async def list_logs(
    actor: str = "", action: str = "", q: str = "", date_from: str = "", date_to: str = "",
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    owner: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    # LEFT JOIN User so request-logged rows (actor_email blank) still resolve to an email
    stmt = select(AuditLog, User.email).join(User, User.id == AuditLog.actor_id, isouter=True)
    if action.strip():
        stmt = stmt.where(AuditLog.action.like(f"%{action.strip()}%"))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(AuditLog.target).like(like), func.lower(AuditLog.detail).like(like)))
    if actor.strip():
        like = f"%{actor.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(AuditLog.actor_email).like(like), func.lower(User.email).like(like)))
    lo, hi = _bound(date_from), _bound(date_to, end=True)
    if lo:
        stmt = stmt.where(AuditLog.ts >= lo)
    if hi:
        stmt = stmt.where(AuditLog.ts <= hi)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(AuditLog.ts.desc()).offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(stmt)).all()
    items = [{
        "id": a.id, "ts": a.ts.isoformat() if a.ts else "",
        "actor": a.actor_email or email or "—",
        "action": a.action, "target": a.target, "detail": a.detail,
    } for a, email in rows]
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@router.get("/actions")
async def distinct_actions(owner: User = Depends(current_owner),
                           session: AsyncSession = Depends(get_async_session)):
    """Distinct action names for the filter dropdown."""
    rows = (await session.execute(select(AuditLog.action).distinct())).all()
    return {"actions": sorted(a for (a,) in rows if a)}
