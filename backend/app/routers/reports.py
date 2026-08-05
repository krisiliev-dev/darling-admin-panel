# -*- coding: utf-8 -*-
"""Справки (#84) — read-only proxy over Barsy's report pages. Barsy is the system of record and its
~200 Справки are all grid-queryable by page-id; the panel exposes a VERIFIED whitelist (every id
below answered a live structure probe on 2026-08-05 — names from the survey that 501'd were dropped,
never guessed). Columns come from the report's own page structure (BG titles), cached in-process.
No filters in v1: each report returns Barsy's default window; filter pass-through comes once real
sales data exists to verify shapes against (money/verification rule)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

from ..core import barsy
from ..models import User
from ..upstream import upstream
from ..users import current_owner

router = APIRouter(prefix="/api/reports", tags=["reports"])

# key -> (page_id, BG title, group). VERIFIED live (structure probe) — extend only after probing.
REPORTS: dict[str, tuple[str, str, str]] = {
    "sales_by_articles": ("reports_sales_by_articles", "Продажби по артикули", "Продажби"),
    "sales_by_cats": ("reports_sales_by_cats", "Продажби по категории", "Продажби"),
    "sales_by_paymethods": ("reports_sales_by_payment_methods", "Продажби по начини на плащане", "Продажби"),
    "sales_storno": ("reports_sales_by_storno", "Сторно продажби", "Продажби"),
    "sales_by_users": ("reports_sales_by_users", "Продажби по потребители", "Продажби"),
    "payments": ("reports_payments", "Плащания", "Плащания"),
    "payments_by_periods": ("reports_payments_by_periods", "Плащания по периоди", "Плащания"),
    "payments_by_methods": ("reports_payments_by_payment_methods", "Плащания по начини", "Плащания"),
    "store_amounts_by_date": ("reports_store_amounts_by_date", "Наличности към дата", "Склад"),
    "storeloads": ("reports_storeloads", "Зареждания", "Склад"),
    "storeloads_by_suppliers": ("reports_storeloads_by_suppliers", "Зареждания по доставчици", "Склад"),
    "store_moves": ("reports_store_moves", "Прехвърляния", "Склад"),
    "store_outs": ("reports_store_outs", "Изписвания", "Склад"),
    "delivery_price_changes": ("reports_storeloads_changed_deliveryprices", "Промени в доставни цени", "Склад"),
    "sell_price_changes": ("reports_articles_actual_prices", "Промени в продажни цени", "Артикули"),
    "delivery_prices": ("reports_articles_delivery_prices", "Доставни цени по артикули", "Артикули"),
    "site_sync": ("reports_site_sync", "Синхронизация с е-магазина", "Артикули"),
    "clients_balance": ("reports_clients_balance", "Клиенти — баланс", "Клиенти"),
    "clients_totals": ("reports_clients_totals", "Клиенти — обороти", "Клиенти"),
    "logs": ("reports_logs", "Дневник събития", "Дневник"),
    "logs_by_users": ("reports_logs_by_users", "Дневник по потребители", "Дневник"),
    "invoices_registry": ("invoices_index", "Фактури — регистър", "Документи"),
}

_STRUCT_TTL = 3600.0
_struct_cache: dict[str, tuple[float, str, list[dict]]] = {}   # page_id -> (ts, struct_id, columns)


def _title(t) -> str:
    return t if isinstance(t, str) else ""


def _structure(page_id: str) -> tuple[str, list[dict]]:
    now = time.time()
    hit = _struct_cache.get(page_id)
    if hit and now - hit[0] < _STRUCT_TTL:
        return hit[1], hit[2]
    r = barsy.call(page_id, {"params": {"bid": 1}}, bid=1)
    for c in (r.get("content") or []) if isinstance(r, dict) else []:
        if isinstance(c, dict) and c.get("type") == "eStructList":
            data = c.get("data") or {}
            els = data.get("elements") or []
            items = els if isinstance(els, list) else list(els.values())
            cols = [{"name": e.get("name"), "title": _title(e.get("title")) or e.get("name")}
                    for e in items if isinstance(e, dict) and e.get("name")]
            struct_id = c.get("id") or "eStructList_1"
            _struct_cache[page_id] = (now, struct_id, cols)
            return struct_id, cols
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Справката не върна табличен модел.")


@router.get("")
async def list_reports(user: User = Depends(current_owner)) -> dict:
    groups: dict[str, list] = {}
    for key, (_pid, title, group) in REPORTS.items():
        groups.setdefault(group, []).append({"key": key, "title": title})
    return {"groups": [{"group": g, "reports": rs} for g, rs in groups.items()]}


@router.get("/{key}")
async def read_report(key: str, user: User = Depends(current_owner),
                      rows: int = Query(100, ge=1, le=500), page: int = Query(1, ge=1)) -> dict:
    if key not in REPORTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава справка.")
    page_id, title, group = REPORTS[key]
    with upstream("Barsy справка"):
        struct_id, cols = await run_in_threadpool(_structure, page_id)
        v = await run_in_threadpool(
            barsy.call, page_id,
            {"action_type": "values", "active_struct_id": struct_id, "rows": rows, "page_num": page})
    data = v if isinstance(v, dict) else {}
    _r = data.get("rows")
    out_rows: list = _r if isinstance(_r, list) else []
    # keep the column list tight: only columns that actually appear in the data (reports carry
    # 100+ detail_N columns that are empty for most rows — noise in a generic table)
    present: set = set()
    for r in out_rows:
        if isinstance(r, dict):
            present.update(k for k, val in r.items() if val not in (None, ""))
    columns = [c for c in cols if c["name"] in present] if out_rows else cols[:12]
    return {"key": key, "title": title, "group": group, "columns": columns,
            "rows": out_rows, "total": data.get("total"), "page": page, "per_page": rows}
