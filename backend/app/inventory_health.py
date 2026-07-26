# -*- coding: utf-8 -*-
"""Здраве на инвентара — inventory-health logic ported from the old-ERP /analytics page.

The headline insight that page revealed: ~a quarter of inventory value is frozen in stock that
doesn't move (DSI 317 days, dead stock 25.7%). The launch-day version of that insight comes from
LOT AGEING — Barsy's `Store_amounts_lots` carries each stock lot's receipt date, so
days-in-warehouse = today − receipt_date needs NO sales history and is live from day one
(per the Hermes research). Sales-history-dependent metrics (turnover/DSI/GMROI/velocity) come
later, computed live as Barsy accrues history and seeded once from the Microinvest snapshot.

This module is PURE (no FastAPI, no I/O) so the bucketing/aggregation is unit-tested directly. The
Barsy fetch + field mapping lives in routers/inventory_health.py and hands normalized lots in here.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

# Ageing buckets by days-in-warehouse (receipt → today). Order matters (first match wins upward).
# 365+ is the clearest "dead / overflowing warehouse" signal; 180+ is the default alert threshold.
BUCKETS = [
    ("0-90", "0–90 дни", 0, 90),
    ("90-180", "90–180 дни", 90, 180),
    ("180-365", "180–365 дни", 180, 365),
    ("365+", "365+ дни", 365, None),
]
DEFAULT_STALE_DAYS = 180  # lots at/over this age are surfaced in the manager alert

# Markdown ladder: the longer stock has sat, the deeper the suggested clearance discount. Tiers are
# by days-in-warehouse; the manager still decides — this is a recommendation, not an auto-apply.
MARKDOWN_LADDER = ((365, 50), (180, 30), (90, 15))  # (min_days, suggested_pct), checked high→low


def markdown_for(days: int) -> int:
    """Suggested clearance discount % for stock that has sat `days` in the warehouse (0 if fresh)."""
    for min_days, pct in MARKDOWN_LADDER:
        if days >= min_days:
            return pct
    return 0


def parse_date(v) -> date | None:
    """Barsy dates arrive as 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (or already a date). Be lenient."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    # last resort: leading ISO date
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _bucket_key(days: int) -> str:
    for key, _label, lo, hi in BUCKETS:
        if days >= lo and (hi is None or days < hi):
            return key
    return BUCKETS[-1][0]


def aging_report(
    lots: Iterable[dict],
    today: date | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    top: int = 100,
) -> dict:
    """Aggregate normalized lots into ageing-by-value buckets + a stale-item alert list.

    Each lot dict: {article_id, name, supplier, qty, unit_cost, sell_price, received}
      - qty > 0 (on-hand units of that lot), unit_cost in EUR, received is a date (or None).
    A lot with no receipt date can't be aged → counted under `undated` (not silently dropped).
    Per-article the stale list keeps the OLDEST lot age and sums the value of lots ≥ stale_days,
    sorted worst-first by frozen capital — that's what the manager acts on (deepen the discount).
    """
    today = today or date.today()
    buckets = {k: {"value": 0.0, "units": 0.0, "lots": 0} for k, *_ in BUCKETS}
    total_value = total_units = 0.0
    total_lots = undated_lots = 0
    undated_value = 0.0
    articles: dict = {}
    seen_articles: set = set()

    for lot in lots:
        qty = _num(lot.get("qty"))
        if qty is None or qty <= 0:
            continue
        cost = _num(lot.get("unit_cost")) or 0.0
        value = qty * cost
        aid = lot.get("article_id")
        seen_articles.add(aid)
        total_units += qty
        total_value += value
        total_lots += 1

        received = lot.get("received")
        if not isinstance(received, date):
            received = parse_date(received)
        if received is None:
            undated_lots += 1
            undated_value += value
            continue
        days = (today - received).days
        if days < 0:
            days = 0
        b = buckets[_bucket_key(days)]
        b["value"] += value
        b["units"] += qty
        b["lots"] += 1

        if days >= stale_days:
            a = articles.get(aid)
            if a is None:
                a = articles[aid] = {
                    "article_id": aid, "name": lot.get("name") or "",
                    "supplier": lot.get("supplier") or "",
                    "oldest_days": days, "qty": 0.0, "value": 0.0,
                    "unit_cost": cost, "sell_price": _num(lot.get("sell_price")),
                }
            a["oldest_days"] = max(a["oldest_days"], days)
            a["qty"] += qty
            a["value"] += value

    stale_items = sorted(articles.values(), key=lambda x: x["value"], reverse=True)
    stale_value = round(sum(a["value"] for a in stale_items), 2)
    stale_units = round(sum(a["qty"] for a in stale_items), 2)
    for a in stale_items:
        a["qty"] = round(a["qty"], 2)
        a["value"] = round(a["value"], 2)
        # age-based clearance suggestion; new price only when a sell price is known
        a["suggested_markdown_pct"] = markdown_for(a["oldest_days"])
        sp = a.get("sell_price")
        a["suggested_price"] = (round(sp * (1 - a["suggested_markdown_pct"] / 100), 2)
                                if sp else None)

    return {
        "as_of": today.isoformat(),
        "currency": "EUR",
        "buckets": [
            {"key": k, "label": lbl, "days_min": lo, "days_max": hi,
             "value": round(buckets[k]["value"], 2), "units": round(buckets[k]["units"], 2),
             "lots": buckets[k]["lots"]}
            for k, lbl, lo, hi in BUCKETS
        ],
        "undated": {"lots": undated_lots, "value": round(undated_value, 2)},
        "totals": {
            "value": round(total_value, 2), "units": round(total_units, 2),
            "lots": total_lots, "articles": len(seen_articles),
        },
        "stale": {
            "threshold_days": stale_days,
            "value": stale_value, "units": stale_units, "articles": len(stale_items),
            "pct_of_value": round(stale_value / total_value * 100, 1) if total_value else 0.0,
            "items": stale_items[:top],
            "truncated": max(0, len(stale_items) - top),
        },
    }


def _num(v) -> float | None:
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None
