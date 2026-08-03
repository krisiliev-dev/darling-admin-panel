# -*- coding: utf-8 -*-
"""Доставки — RECEIVING goods, BARSY-FIRST. Barsy is the system of record, so:
  • suppliers            → Suppliers_getlist
  • product picker       → Articles_getlist (fills as approved products push to Barsy)
  • очаквана доставка     → a DRAFT storeload (Storeloads_create action_type='save')
  • получена доставка     → Storeloads_close (quantities enter stock)
  • reorder suggestions  → Store_amounts (below minimum)
Nothing supplier/order-related is stored locally — only a panel audit entry (governance).

NOT the place for a ЗАЯВКА. This module used to call a draft storeload a „заявка", which conflated
two different documents: a storeload is goods ARRIVING, a заявка is goods being ASKED FOR. Barsy has
its own supplier-order entity (`requests`) with its own Нови → Изпратени → Заредени lifecycle — see
docs/BARSY_ZAYAVKI_CONTRACT.md and app/services/barsy_requests.py. Заявки live in routers/zayavki.py."""
import datetime
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy, barsy_write_live

_GATED = "Barsy запис е изключен (BARSY_WRITE_LIVE=0) — поръчката не е записана в ERP."
from ..db import get_async_session
from ..models import User
from ..upstream import upstream
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/purchasing", tags=["purchasing"])


@lru_cache(maxsize=1)
def _default_depot() -> int:
    """The store's depot id (single-store business). Discovered once from Barsy."""
    try:
        deps = barsy.call("Depots_getlist", {})
        if isinstance(deps, list) and deps:
            return int(deps[0].get("depot_id") or deps[0].get("id") or 1)
    except Exception:
        pass
    return 1


# ---------------------------------------------------------------- suppliers / catalog ---
@router.get("/suppliers")
def suppliers(user: User = Depends(current_active_user)):
    """Registered suppliers from Barsy."""
    with upstream("Barsy доставчици"):
        rows = barsy.call("Suppliers_getlist", {"extra_properties": ["min"]})
    out = [{"supplier_id": r.get("supplier_id"), "supplier_name": r.get("supplier_name", "")}
           for r in (rows or []) if not r.get("delete_flag")]
    out.sort(key=lambda s: s["supplier_name"])
    return {"suppliers": out}


@router.get("/articles")
def articles(
    user: User = Depends(current_active_user),
    q: str = Query("", description="търсене по име или код"),
    limit: int = Query(40, ge=1, le=100),
):
    """Barsy catalog articles for the PO picker (fills as approved products push to Barsy)."""
    filters: dict = {}
    if q:
        # numeric-ish → try code, else name
        if q.strip().isdigit():
            filters["code"] = q.strip()
        else:
            filters["article_name"] = q.strip()
    with upstream("Barsy артикули"):
        rows = barsy.call("Articles_getlist", {"filters": filters, "length": limit})
    items = []
    for a in (rows or [])[:limit]:
        items.append({"article_id": a.get("article_id"), "code": a.get("code") or "",
                      "name": a.get("article_name") or "",
                      "price": a.get("actual_price")})
    return {"articles": items}


@router.get("/reorder")
def reorder(user: User = Depends(current_active_user)):
    """Reorder suggestions from Barsy stock at/under its minimum (empty until stock lands)."""
    with upstream("Barsy наличности"):
        out = barsy.call("Store_amounts", {"action_type": "values", "page_num": 1, "rows": 500,
                                           "filters": {"below_min": 1}})
    items = []
    for r in (out or {}).get("rows", []):
        try:
            amt = float(r.get("store_amount") or 0)
            mn = float(r.get("min_amount") or 0)
        except (TypeError, ValueError):
            continue
        items.append({"article_id": r.get("article_id"), "name": r.get("article_name") or "",
                      "amount": amt, "min_amount": mn, "unit": r.get("amount_type_name_short") or "",
                      "suggested_qty": max(round(mn * 2 - amt, 2), mn) if mn > 0 else 0})
    return {"count": len(items), "items": items}


# --------------------------------------------------- purchase orders = draft storeloads ---
class POLine(BaseModel):
    article_id: int
    name: str = Field("", max_length=256)
    amount: float = Field(1, ge=0, le=1_000_000)        # quantity
    current_price: float = Field(0.0, ge=0, le=1_000_000)  # delivery/cost price €


class POCreate(BaseModel):
    supplier_id: int
    note: str = Field("", max_length=1000)
    # bound the order — a real заявка is a few hundred lines at most; the cap stops one
    # request fanning out to an unbounded Barsy Storeloads_create payload.
    lines: list[POLine] = Field(max_length=2000)
    close: bool = False      # True = receive into stock now; False = draft (заявка)


@router.get("/orders")
def list_orders(user: User = Depends(current_active_user)):
    """Storeloads (заявки + получени доставки) from Barsy. status: 0=draft, 1=closed."""
    with upstream("Barsy зареждания"):
        rows = barsy.call("Storeloads_getlist", {})
    out = []
    for r in (rows or []):
        st = r.get("status")
        out.append({
            "id": r.get("store_load_id"), "supplier_id": r.get("supplier_id"),
            "supplier": r.get("supplier_name") or "",
            "status": "closed" if st == 1 else "draft",
            "doc_type": r.get("doc_type_name") or "", "doc_num": r.get("doc_num") or "",
            "doc_date": r.get("doc_date") or "", "total": r.get("total"),
        })
    return {"orders": out}


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    req: POCreate,
    actor: User = Depends(require_roles("staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a supplier order in Barsy as a draft storeload (or receive-into-stock if close)."""
    if not req.lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заявката няма редове.")
    header = {
        "depot_id": await run_in_threadpool(_default_depot), "supplier_id": req.supplier_id,
        "doc_date": datetime.date.today().isoformat(), "doc_type_id": 1,
        "operation_type": 1, "description": req.note,
    }
    rows = [{"article_id": ln.article_id, "original_article_name": ln.name,
             "amount": ln.amount, "current_price": ln.current_price} for ln in req.lines]
    body = {"store_load": header, "articles": rows,
            "action_type": "save_and_close" if req.close else "save"}
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED,
                "would_write": {"lines": len(rows), "close": req.close}}
    with upstream("Barsy зареждане"):
        new_id = await run_in_threadpool(barsy.call, "Storeloads_create", body)
    await log_action(session, actor, "po.create", target=str(req.supplier_id),
                     detail=f"storeload={new_id} lines={len(req.lines)} close={req.close}")
    return {"ok": True, "storeload_id": new_id, "status": "closed" if req.close else "draft"}


@router.post("/orders/{storeload_id}/close")
async def close_order(
    storeload_id: int,
    actor: User = Depends(require_roles("staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Finalize a draft storeload — quantities enter Barsy stock (доставката е получена)."""
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED, "status": "draft"}
    with upstream("Barsy приключване"):
        await run_in_threadpool(barsy.call, "Storeloads_close", {"id": storeload_id})
    await log_action(session, actor, "po.close", target=str(storeload_id), detail="received")
    return {"ok": True, "status": "closed"}
