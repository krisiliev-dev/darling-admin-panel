# -*- coding: utf-8 -*-
"""Кошници — live + abandoned Shopify checkouts, mirrored READ-ONLY from the checkouts webhooks
(app.routers.webhooks._process_checkout). Answers "how many people have items in their cart right
now" and lists abandoned checkouts. Never touches Barsy."""
import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..models import Checkout, User
from ..users import current_active_user

router = APIRouter(prefix="/api/carts", tags=["carts"])

_ABANDON_MIN = 30      # a not-completed checkout untouched for >30 min counts as abandoned


def _age_min(ts: datetime.datetime | None, now: datetime.datetime) -> float | None:
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return (now - ts).total_seconds() / 60.0


@router.get("")
async def list_carts(session: AsyncSession = Depends(get_async_session),
                     user: User = Depends(current_active_user)):
    """Open checkouts split into active (recently touched) vs abandoned (idle > 30 min)."""
    rows = (await session.execute(
        select(Checkout).where(Checkout.completed.is_(False))
        .order_by(Checkout.updated_at.desc()))).scalars().all()
    now = datetime.datetime.now(datetime.UTC)
    active: list[dict] = []
    abandoned: list[dict] = []
    for r in rows:
        upd = r.updated_at or r.created_at
        item = {"token": r.token, "email": r.email, "phone": r.phone, "name": r.name,
                "total_price": r.total_price, "currency": r.currency, "item_count": r.item_count,
                "updated_at": upd.isoformat() if upd else None}
        age = _age_min(upd, now)
        (abandoned if (age is not None and age > _ABANDON_MIN) else active).append(item)
    return {"active": active, "abandoned": abandoned,
            "active_count": len(active), "abandoned_count": len(abandoned)}
