# -*- coding: utf-8 -*-
"""Живи метрики от продажбена история — the go-forward counterpart to the seeded historical
diagnosis. Once Barsy accrues sales history, `Reports_sales_by_articles` (units + revenue + cost
per article over a window) paired with `Store_amounts` (on-hand + cost) yields the SAME insight
set the old /analytics page had — but LIVE and actionable:

  per-article:  velocity (units/day) · days-of-cover · needs-reorder · dead (no sale in window)
  portfolio:    turnover · DSI · GMROI · dead-stock value/%

The actionable lists this produces drive buttons/alerts in the panel:
  - reorder[]  → "Създай заявка" into the Purchasing/PO module (fast movers about to srun out)
  - dead[]     → "Отстъпка" into the repricing flow (frozen capital, deepen the discount)
  - top_sellers[] → keep in stock / feature on the storefront

Pure + I/O-free so it's unit-tested directly; the Barsy fetch + field mapping lives in the router.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable, Optional

DEFAULT_WINDOW = 365      # days of sales history to measure velocity over
DEFAULT_LEAD_DAYS = 14    # supplier lead time (reorder point horizon) — overridable per call
DEFAULT_SAFETY_DAYS = 7   # safety-stock buffer on top of lead time
DEFAULT_COVERAGE_DAYS = 30  # how many days of supply a reorder should top the shelf up TO


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute(
    sales: Iterable[dict],
    stock: Iterable[dict],
    window_days: int = DEFAULT_WINDOW,
    lead_days: int = DEFAULT_LEAD_DAYS,
    safety_days: int = DEFAULT_SAFETY_DAYS,
    coverage_days: int = DEFAULT_COVERAGE_DAYS,
    today: Optional[date] = None,
    top: int = 50,
) -> dict:
    """sales rows: {article_id, name, units, revenue, cogs}; stock rows: {article_id, name, stock,
    unit_cost, sell_price}. Joined by article_id. window_days > 0. Returns portfolio KPIs + the
    three actionable lists. Empty inputs → a valid empty report (pre-launch safe)."""
    today = today or date.today()
    window_days = max(1, window_days)
    horizon = lead_days + safety_days

    s_by_id: dict = {}
    for r in sales:
        aid = r.get("article_id")
        if aid is None:
            continue
        s_by_id[aid] = {
            "units": _num(r.get("units")) or 0.0,
            "revenue": _num(r.get("revenue")) or 0.0,
            "cogs": _num(r.get("cogs")) or 0.0,
            "name": r.get("name"),
        }

    # portfolio revenue/COGS span ALL sales in the window (incl. now sold-out items), not just
    # what's currently stocked — otherwise turnover understates. Inventory value spans stock.
    total_rev = sum(s["revenue"] for s in s_by_id.values())
    total_cogs = sum(s["cogs"] for s in s_by_id.values())

    items = []
    inv_value = 0.0
    for r in stock:
        aid = r.get("article_id")
        if aid is None:
            continue
        qty = _num(r.get("stock")) or 0.0
        cost = _num(r.get("unit_cost")) or 0.0
        value = qty * cost
        inv_value += value
        s = s_by_id.get(aid, {})
        units = s.get("units", 0.0)
        velocity = units / window_days
        cover = (qty / velocity) if velocity > 0 else None
        reorder_point = velocity * horizon
        needs_reorder = velocity > 0 and qty <= reorder_point
        is_dead = qty > 0 and units == 0
        # order the shelf back up to (lead + safety + coverage) days of supply, net of what's on hand
        order_up_to = velocity * (horizon + coverage_days)
        suggested_qty = max(0, math.ceil(order_up_to - qty)) if velocity > 0 else 0
        items.append({
            "article_id": aid, "name": r.get("name") or s.get("name") or "",
            "stock": round(qty, 2), "unit_cost": round(cost, 4),
            "sell_price": _num(r.get("sell_price")),
            "units_sold": round(units, 2), "velocity": round(velocity, 4),
            "days_of_cover": round(cover, 1) if cover is not None else None,
            "reorder_point": round(reorder_point, 1), "suggested_qty": suggested_qty,
            "needs_reorder": needs_reorder, "is_dead": is_dead,
            "value": round(value, 2), "revenue": round(s.get("revenue", 0.0), 2),
        })

    # annualise to a 365-day basis so turnover / DSI / GMROI are comparable regardless of the window
    annual_cogs = total_cogs * 365.0 / window_days
    turnover = (annual_cogs / inv_value) if inv_value > 0 else None
    dsi = (365.0 / turnover) if turnover else None
    # GMROI must be annualised on the SAME basis as turnover — dividing the raw window-cumulative
    # gross margin by inventory overstates it by window/365 on any non-365-day window (up to ~3× at
    # the 1095-day max). Annualise the margin so a 90-day and a 730-day window agree.
    annual_margin = (total_rev - total_cogs) * 365.0 / window_days
    gmroi = (annual_margin / inv_value) if inv_value > 0 else None
    dead_items = [x for x in items if x["is_dead"]]
    dead_value = sum(x["value"] for x in dead_items)

    reorder = sorted([x for x in items if x["needs_reorder"]],
                     key=lambda x: (x["days_of_cover"] if x["days_of_cover"] is not None else 1e9))
    dead = sorted(dead_items, key=lambda x: x["value"], reverse=True)
    top_sellers = sorted([x for x in items if x["units_sold"] > 0],
                         key=lambda x: x["units_sold"], reverse=True)

    return {
        "as_of": today.isoformat(), "currency": "EUR", "window_days": window_days,
        "lead_days": lead_days, "safety_days": safety_days,
        "kpi": {
            "inv_value": round(inv_value, 2),
            "turnover": round(turnover, 2) if turnover is not None else None,
            "dsi": round(dsi) if dsi is not None else None,
            "gmroi": round(gmroi, 2) if gmroi is not None else None,
            "dead_value": round(dead_value, 2),
            "dead_pct": round(dead_value / inv_value * 100, 1) if inv_value > 0 else 0.0,
            "dead_n": len(dead_items),
            "reorder_n": len(reorder),
            "revenue": round(total_rev, 2), "units": round(sum(x["units_sold"] for x in items), 2),
            "skus": len(items),
        },
        "reorder": reorder[:top],
        "dead": dead[:top],
        "top_sellers": top_sellers[:top],
        "has_sales": bool(s_by_id),
    }
