# -*- coding: utf-8 -*-
"""Клиенти — behavioral customer insights from ORDER data (no sensitive demographics). Answers the
"know our base / target better" question with what we actually have: new vs returning, geography,
category/product affinity, COD share, delivery-success. Empty until the store is live; fills as orders
flow. Computed from Shopify orders + our OrderOutcome — the low-risk, high-signal cut."""
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..core import shopify_orders
from ..db import get_async_session
from ..models import OrderOutcome, User
from ..users import current_active_user

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@router.get("")
async def insights(
    user: User = Depends(current_active_user),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(250, ge=1, le=1000),
    session: AsyncSession = Depends(get_async_session),
):
    # Shopify being down must DEGRADE, not 500 — this is the Анализи landing tab, and the order queue
    # (orders.py list_orders) already handles the identical call this way. A 500 here renders a bare
    # "Грешка при зареждане" over the whole dashboard for what is only a missing data source.
    try:
        orders = await run_in_threadpool(shopify_orders.fetch_orders, limit)
        warning = ""
    except Exception as e:
        orders, warning = [], f"Shopify недостъпен: {str(e)[:120]}"
    n = len(orders)
    revenue = round(sum(_f(o.get("total")) for o in orders), 2)
    cod_orders = sum(1 for o in orders if o.get("cod"))

    # new vs returning (by phone)
    phone_counts = Counter(o.get("phone") for o in orders if o.get("phone"))
    customers = len(phone_counts)
    returning = sum(1 for c in phone_counts.values() if c > 1)

    # orders + revenue by day (last `days`)
    by_day: dict = defaultdict(lambda: {"orders": 0, "revenue": 0.0})
    for o in orders:
        d = str(o.get("created") or "")[:10]
        if d:
            by_day[d]["orders"] += 1
            by_day[d]["revenue"] += _f(o.get("total"))
    day_series = [{"date": k, "orders": v["orders"], "revenue": round(v["revenue"], 2)}
                  for k, v in sorted(by_day.items())][-days:]

    # geography + product affinity
    top_cities = [{"city": c, "orders": k} for c, k in
                  Counter(o.get("city") for o in orders if o.get("city")).most_common(8)]
    prod: Counter = Counter()
    for o in orders:
        for it in (o.get("items") or []):
            if it.get("title"):
                prod[it["title"][:40]] += int(it.get("qty") or 1)
    top_products = [{"name": nm, "qty": q} for nm, q in prod.most_common(10)]

    # delivery success from our own outcomes (post-launch)
    rows = (await session.execute(select(OrderOutcome))).scalars().all()
    delivered = sum(1 for r in rows if r.outcome == "delivered")
    refused = sum(1 for r in rows if r.outcome == "refused")
    finished = delivered + refused

    return {
        "warning": warning,      # non-empty => the numbers are incomplete, not genuinely zero
        "orders": n,
        "revenue": revenue,
        "aov": round(revenue / n, 2) if n else 0.0,
        "cod_share": round(cod_orders / n * 100, 1) if n else 0.0,
        "customers": {"total": customers, "returning": returning,
                      "repeat_rate": round(returning / customers * 100, 1) if customers else 0.0},
        "by_day": day_series,
        "top_cities": top_cities,
        "top_products": top_products,
        "delivery": {"delivered": delivered, "refused": refused,
                     "rto_rate": round(refused / finished * 100, 1) if finished else 0.0},
    }
