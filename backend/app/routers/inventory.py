# -*- coding: utf-8 -*-
"""Наличности — live stock from Barsy (Store_amounts) with low-stock detection.
Barsy is nearly empty today (products are created as they clear intake approval), so this
shows an empty state and fills in as the catalog lands. Reads only — no writes."""
from fastapi import APIRouter, Depends, Query

from ..core import barsy
from ..upstream import upstream
from ..users import current_active_user

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch(page: int, rows: int, low_only: bool):
    filters = {}
    if low_only:
        filters["below_min"] = 1  # Barsy flag: quantity under the reorder minimum
    body = {"action_type": "values", "page_num": page, "rows": rows, "filters": filters}
    with upstream("Barsy наличности"):
        out = barsy.call("Store_amounts", body)
    return out or {}


@router.get("")
def inventory(
    user=Depends(current_active_user),
    q: str = Query("", description="търсене по име"),
    low_only: bool = Query(False),
    page: int = Query(1, ge=1),
    rows: int = Query(100, ge=1, le=500),
):
    out = _fetch(page, rows, low_only)
    items = []
    low = zero = 0
    for r in (out.get("rows") or []):
        amount = _num(r.get("store_amount"))
        minimum = _num(r.get("min_amount"))
        # low = flagged by Barsy, or at/under an explicit minimum
        is_low = (r.get("below_min") == 1) or (
            minimum is not None and amount is not None and amount <= minimum)
        is_zero = amount is not None and amount <= 0
        if is_low:
            low += 1
        if is_zero:
            zero += 1
        name = r.get("article_name") or ""
        if q and q.lower() not in name.lower():
            continue
        items.append({
            "article_id": r.get("article_id"),
            "name": name,
            "amount": amount,
            "unit": r.get("amount_type_name_short") or "",
            "min_amount": minimum,
            "avg_cost": _num(r.get("avg_delivery_price")),
            "low": bool(is_low),
            "zero": bool(is_zero),
        })
    return {
        "total": out.get("total", 0),
        "summary": {"tracked": out.get("total", 0), "low": low, "zero": zero},
        "items": items,
    }
