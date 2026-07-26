# -*- coding: utf-8 -*-
"""Начало — the operator's landing. Quick-glance KPIs for a chosen period (днес/7д/30д), an
attention strip, and the orders that still need packing. Everything degrades to empty/zero before
the store launches. Sales come from Shopify orders; cost (for Приход = Оборот − себестойност) is a
cheap LOCAL lookup from analytics.price_history; reorder count is a light Barsy call (guarded)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from ..core import barsy, db, shopify_orders
from ..upstream import upstream
from ..users import current_active_user

router = APIRouter(prefix="/api/summary", tags=["home"])

_PERIODS = {"today": 1, "7d": 7, "30d": 30}
_PENDING = {"new", "picking", "packed"}   # op_status values that still need packing/sending


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _cost_by_code() -> dict:
    """{code: cost_price} from the latest price_history snapshot (local sqlite — no Barsy call)."""
    out: dict = {}
    c = db.connect()
    try:
        rows = c.execute(
            "SELECT code, cost_price FROM price_history WHERE snapshot_date="
            "(SELECT MAX(snapshot_date) FROM price_history)").fetchall()
        for code, cost in rows:
            if cost is not None:
                out[str(code)] = _f(cost)
    except Exception:  # noqa: BLE001 — no snapshot yet → empty cost map → profit == turnover
        pass
    finally:
        c.close()
    return out


def _within(created: str, days: int, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(created).replace("Z", "+00:00")) >= now - timedelta(days=days)
    except (ValueError, TypeError):
        return False


def _reorder_count() -> int:
    """# articles under their Barsy reorder minimum (light call, guarded)."""
    try:
        with upstream("Barsy наличности"):
            out = barsy.call("Store_amounts", {"action_type": "values", "page_num": 1,
                                               "rows": 1, "filters": {"below_min": 1}})
        return int((out or {}).get("total") or 0)
    except Exception:  # noqa: BLE001
        return 0


@router.get("/home")
async def home(user=Depends(current_active_user), period: str = Query("today")):
    days = _PERIODS.get(period, 1)
    now = datetime.now(UTC)

    try:
        orders = await run_in_threadpool(shopify_orders.fetch_orders, 500)
        smap = await run_in_threadpool(shopify_orders.state_map)   # our per-order operational status
        for o in orders:
            st = smap.get(o.get("id"), {})
            o["op_status"] = st.get("status") or "new"
            o["op_status_bg"] = shopify_orders.STATUS_BG.get(o["op_status"], o["op_status"])
    except Exception:  # noqa: BLE001 — store not live yet
        orders = []
    win = [o for o in orders if _within(o.get("created", ""), days, now)]

    turnover = round(sum(_f(o.get("total")) for o in win), 2)
    units = sum(int(_f(it.get("qty"))) for o in win for it in (o.get("items") or []))
    # profit = turnover − COGS. If a sold item has no known cost, COGS is UNDERstated (→ profit
    # OVERstated), so flag it: profit_complete=False lets the UI mark the figure as an estimate.
    cost = await run_in_threadpool(_cost_by_code)
    cogs = 0.0
    profit_complete = True
    for o in win:
        for it in (o.get("items") or []):
            c = cost.get(str(it.get("sku") or ""))
            if c is None:
                profit_complete = False
            else:
                cogs += int(_f(it.get("qty"))) * c
    profit = round(turnover - cogs, 2)

    to_pack = sum(1 for o in win if (o.get("op_status") or "new") in _PENDING)
    done = len(win) - to_pack

    tally: dict = {}
    for o in win:
        for it in (o.get("items") or []):
            k = it.get("title") or it.get("sku") or "?"
            tally[k] = tally.get(k, 0) + int(_f(it.get("qty")))
    top = max(tally.items(), key=lambda x: x[1]) if tally else None

    reorder_n = await run_in_threadpool(_reorder_count)

    # order cards that still need packing (ALL pending, not just this window)
    pending = [o for o in orders if (o.get("op_status") or "new") in _PENDING]
    cards = [{
        "id": o.get("id"), "name": o.get("name"), "total": _f(o.get("total")),
        "customer": o.get("customer") or "", "city": o.get("city") or "",
        "op_status_bg": o.get("op_status_bg") or "",
        "items": [{"title": it.get("title"), "qty": it.get("qty"), "sku": it.get("sku")}
                  for it in (o.get("items") or [])],
    } for o in pending[:12]]

    attention = []
    if to_pack:
        attention.append({"tone": "pending", "text": f"{to_pack} поръчки чакат опаковане", "to": "/orders"})
    if reorder_n:
        attention.append({"tone": "pending", "text": f"{reorder_n} артикула за дозареждане", "to": "/purchasing"})

    return {
        "period": period,
        "kpi": {
            "turnover": turnover, "profit": profit, "profit_complete": profit_complete,
            "units": units,
            "orders_done": done, "orders_to_pack": to_pack,
            "reorder_n": reorder_n,
            "top_product": ({"name": top[0], "units": top[1]} if top else None),
        },
        "attention": attention,
        "to_pack": cards,
        "has_sales": bool(win),
    }
