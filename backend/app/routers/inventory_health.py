# -*- coding: utf-8 -*-
"""Здраве на инвентара — inventory-health endpoints (lot ageing + stale-stock alert).

Reproduces the old-ERP /analytics "warehouse is full of stuff that doesn't move" insight against
Barsy, live from day one. The ageing basis is LOT RECEIPT DATE (`Store_amounts_lots`), so it needs
no sales history — days-in-warehouse = today − receipt. Pure aggregation is in app/inventory_health.py.

FIELD-NAME NOTE: `Store_amounts_lots` row keys are being confirmed with the Hermes/Barsy session.
The uncertain ones (lot date / qty / cost) are resolved through candidate lists below, so once the
exact shape lands it's a one-line edit, and defensive extraction means realistic keys already work.
"""
import json
import os
from datetime import date, timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, Query

from .. import inventory_health as ih
from .. import inventory_metrics as im
from ..core import barsy
from ..upstream import upstream
from ..users import current_active_user

# One-time historical seed baked from the Microinvest export (EUR-converted). It reproduces the
# old-ERP /analytics diagnosis so the panel shows the same insight day one, until Barsy accrues its
# own history. Clearly labelled pre-migration so nobody mistakes it for live Barsy numbers.
_SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed_analytics.json")


@lru_cache(maxsize=1)
def _seed():
    try:
        with open(_SEED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_PRODUCTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed_products.json")
_SORT_FIELDS = {"name", "supplier", "units", "rev", "marg", "stock", "stockval", "last", "cover"}
_TEXT_FIELDS = {"name", "supplier", "last"}


@lru_cache(maxsize=1)
def _products():
    try:
        with open(_PRODUCTS_PATH, encoding="utf-8") as f:
            return json.load(f).get("products", [])
    except (OSError, json.JSONDecodeError):
        return []


def _numf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

router = APIRouter(prefix="/api/inventory-health", tags=["inventory-health"])

# Row keys for Store_amounts_lots. Confirmed by Hermes against the report schema + a live probe
# (dev store empty, so re-verify once real stock exists): receipt date = operation_doc_date, qty =
# amount, per-lot cost = delivery_price. Extra candidates kept as defensive fallbacks (first wins).
_LOT_DATE_KEYS = ("operation_doc_date", "create_date", "load_date", "lot_date", "delivery_date", "date")
_LOT_QTY_KEYS = ("amount", "lot_amount", "store_amount", "amount_sum", "quantity", "qty")
_LOT_COST_KEYS = ("delivery_price", "avg_delivery_price", "lot_price", "current_delivery_price")
_LOT_NAME_KEYS = ("article_name", "name", "good_name")
_LOT_SUPPLIER_KEYS = ("partner_name", "supplier_name", "supplier", "partner")
_LOT_SELL_KEYS = ("current_price", "sell_price", "price_out")
# Bonus data lots also carry: lot_exp_date (Годно до / expiry) → future expiry-alert view; lot_value (batch).

_MAX_PAGES = 60  # safety cap; at 500 rows/page = 30k lots. Truncation is reported, never silent.


def _first(row: dict, keys) -> object:
    for k in keys:
        v = row.get(k)
        if v not in (None, "", 0, "0"):
            return v
    # fall back to the first key that merely exists (covers a legit 0 qty/cost)
    for k in keys:
        if k in row:
            return row.get(k)
    return None


def _normalize(row: dict) -> dict:
    return {
        "article_id": row.get("article_id") or row.get("good_id") or row.get("id"),
        "name": _first(row, _LOT_NAME_KEYS),
        "supplier": _first(row, _LOT_SUPPLIER_KEYS),
        "qty": _first(row, _LOT_QTY_KEYS),
        "unit_cost": _first(row, _LOT_COST_KEYS),
        "sell_price": _first(row, _LOT_SELL_KEYS),
        "received": _first(row, _LOT_DATE_KEYS),
    }


def _fetch_lots(rows_per_page: int = 500) -> tuple[list[dict], bool]:
    """Page through Store_amounts_lots, returning (normalized_lots, truncated)."""
    lots: list[dict] = []
    truncated = False
    with upstream("Barsy партиди (lot ageing)"):
        for page in range(1, _MAX_PAGES + 1):
            body = {"action_type": "values", "page_num": page, "rows": rows_per_page, "filters": {}}
            out = barsy.call("Store_amounts_lots", body) or {}
            batch = out.get("rows") or []
            lots.extend(_normalize(r) for r in batch)
            total = out.get("total")
            if not batch or len(batch) < rows_per_page:
                break
            if isinstance(total, int) and len(lots) >= total:
                break
        else:
            truncated = True
    return lots, truncated


@router.get("/aging")
def aging(
    user=Depends(current_active_user),
    stale_days: int = Query(ih.DEFAULT_STALE_DAYS, ge=30, le=1095),
    top: int = Query(100, ge=1, le=1000),
):
    """Lot-ageing buckets by capital-at-cost + the stale-stock alert list (worst frozen capital first)."""
    lots, truncated = _fetch_lots()
    report = ih.aging_report(lots, stale_days=stale_days, top=top)
    report["source"] = "barsy:Store_amounts_lots"
    report["truncated_fetch"] = truncated
    return report


# --- go-forward LIVE metrics (Reports_sales_by_articles) ---
# Row keys CONFIRMED by Hermes against the report schema (dev store empty → re-probe at launch to
# lock): cnt = units sold, oborot = revenue, store_amount = on-hand (stock ships IN this report).
# Per-row cost field is uncertain (totals block has delivery_total) → candidate list. First wins.
_SALE_UNIT_KEYS = ("cnt", "units", "amount", "quantity")
_SALE_REV_KEYS = ("oborot", "revenue", "sale_sum", "turnover")
_SALE_COGS_KEYS = ("delivery_total", "delivery_price_sum", "delivery_price", "cost_sum", "cost")
_SALE_STOCK_KEYS = ("store_amount", "amount", "stock")
# Trailing window maps to a `ref_date: [from, to]` RANGE array (Hermes-confirmed live shape) — the
# clean mapping for the window selector, not calendar-year-pinned. time_interval left to its default.


def _norm_sale(row: dict) -> dict:
    return {
        "article_id": row.get("article_id") or row.get("good_id") or row.get("id"),
        "name": _first(row, _LOT_NAME_KEYS),
        "units": _first(row, _SALE_UNIT_KEYS),
        "revenue": _first(row, _SALE_REV_KEYS),
        "cogs": _first(row, _SALE_COGS_KEYS),  # delivery_total = "Сума себест (без ДДС)"
        "store_amount": _first(row, _SALE_STOCK_KEYS),
    }


def _fetch_sales(window_days: int, today: date) -> list[dict]:
    """Reports_sales_by_articles over the trailing window [today-window, today] via a ref_date range.
    Any upstream failure → [] so the endpoint degrades to a stock-only (pre-launch) report."""
    d_from = (today - timedelta(days=window_days)).isoformat()
    body = {"action_type": "values", "page_num": 1, "rows": 5000,
            "ref_date": [d_from, today.isoformat()], "filters": {}}
    try:
        with upstream("Barsy продажби по артикул"):
            out = barsy.call("Reports_sales_by_articles", body) or {}
        return [_norm_sale(r) for r in (out.get("rows") or [])]
    except Exception:  # noqa: BLE001 — no sales history yet is normal pre-launch
        return []


def _fetch_stock() -> list[dict]:
    rows: list[dict] = []
    with upstream("Barsy наличности"):
        for page in range(1, _MAX_PAGES + 1):
            out = barsy.call("Store_amounts", {"action_type": "values", "page_num": page,
                                               "rows": 500, "filters": {}}) or {}
            batch = out.get("rows") or []
            for r in batch:
                rows.append({
                    "article_id": r.get("article_id"),
                    "name": r.get("article_name"),
                    "stock": r.get("store_amount"),
                    "unit_cost": r.get("avg_delivery_price"),
                    "sell_price": r.get("current_price"),
                })
            if len(batch) < 500:
                break
    return rows


@router.get("/metrics")
def metrics(
    user=Depends(current_active_user),
    window: int = Query(im.DEFAULT_WINDOW, ge=30, le=1095),
    lead_days: int = Query(im.DEFAULT_LEAD_DAYS, ge=0, le=120),
    safety_days: int = Query(im.DEFAULT_SAFETY_DAYS, ge=0, le=120),
    coverage: int = Query(im.DEFAULT_COVERAGE_DAYS, ge=1, le=365),
    top: int = Query(50, ge=1, le=500),
):
    """LIVE inventory metrics from Barsy sales history + stock: per-article velocity/cover/reorder
    (with suggested order qty), portfolio turnover/DSI/GMROI/dead. `has_sales:false` pre-launch."""
    today = date.today()
    sales = _fetch_sales(window, today)
    stock = _fetch_stock()
    return im.compute(sales, stock, window_days=window, lead_days=lead_days,
                      safety_days=safety_days, coverage_days=coverage, today=today, top=top)


@router.get("/diagnosis")
def diagnosis(user=Depends(current_active_user)):
    """Historical business diagnosis seeded once from Microinvest (EUR): turnover/DSI/GMROI, dead
    stock, monthly trend, ABC, supplier breakdown, worst dead products. `available:false` if the
    seed is missing. These are PRE-MIGRATION figures — the frontend labels them as such."""
    s = _seed()
    if not s:
        return {"available": False}
    return {"available": True, **s}


@router.get("/products")
def products(
    user=Depends(current_active_user),
    supplier: str = Query(""),
    status: str = Query("all", pattern="^(all|dead|active|discontinued)$"),
    q: str = Query(""),
    sort: str = Query("stockval"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """Historical product explorer (pre-migration seed) — filter/sort/paginate SERVER-SIDE over the
    full ~12k rows. Filters: supplier, status (all/dead/active/discontinued), name search."""
    rows = _products()
    if not rows:
        return {"available": False, "total": 0, "products": []}
    ql = q.strip().lower()

    def keep(p):
        if supplier and p.get("supplier") != supplier:
            return False
        if status == "dead" and not p.get("dead"):
            return False
        if status == "active" and (p.get("dead") or p.get("discontinued")):
            return False
        if status == "discontinued" and not p.get("discontinued"):
            return False
        if ql and ql not in (p.get("name") or "").lower():
            return False
        return True

    filtered = [p for p in rows if keep(p)]
    key = sort if sort in _SORT_FIELDS else "stockval"
    if key in _TEXT_FIELDS:
        def sk(p):
            return str(p.get(key) or "").lower()
    else:
        def sk(p):
            n = _numf(p.get(key))
            return n if n is not None else float("-inf")
    filtered.sort(key=sk, reverse=(order != "asc"))

    total = len(filtered)
    start = (page - 1) * per_page
    return {"available": True, "total": total, "page": page, "per_page": per_page,
            "sort": key, "order": order, "products": filtered[start:start + per_page]}
