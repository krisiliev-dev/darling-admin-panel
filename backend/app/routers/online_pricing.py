# -*- coding: utf-8 -*-
"""Онлайн цени — a distinct ONLINE (Shopify) price per product, separate from the physical store
price. The store keeps its base actual_price untouched; the online price is a NATIVE Barsy price
rule scoped to the online delivery types (до адрес + офис). The panel owns the intended online
price (OnlinePrice table) and pushes the full set into that rule as per-article custom prices —
mirroring how pricing.py owns markups. Bulk-ops UX like Ценообразуване: select products (or a whole
category via the filter), then set the online price by exact value / % of the store price / fixed
± amount / clear. Gated behind BARSY_WRITE_LIVE (dry-run until launch). See CHANNEL_PRICING.md."""
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy, barsy_write_live
from ..db import get_async_session
from ..models import OnlinePrice, ProductCache, User
from ..services import barsy_bulk as _bulk
from ..users import current_owner

router = APIRouter(prefix="/api/online-pricing", tags=["online-pricing"])

_GATED = "Barsy запис е изключен (BARSY_WRITE_LIVE=0) — нищо не е записано в ERP."
_RULE_NAME = "Онлайн цени (панел)"            # the single persistent Barsy rule the panel manages
_ONLINE_DELIVERY_TYPES = {2, 4}               # 2=Доставка до адрес, 4=Получаване от офис на куриер
_ALL_DELIVERY_TYPES = [1, 2, 3, 4, 5]         # 1=на място 2=адрес 3=взимане 4=офис 5=за вкъщи
_MODES = {"exact", "pct", "fixed", "clear"}
_BULK_MAX = 2000
_RULE_ID_CACHE: dict = {}                      # memoized {"id": <pricelist_id>}


# ------------------------------------------------------------------ price math ---
def _compute(mode: str, value: float, base: float | None, round_to: int) -> str | None:
    """Target online price (string) for one article, or None to CLEAR the online override.
    exact→value; pct→base*(1+value/100); fixed→base+value. Relative modes need a store baseline;
    result must be > 0. Raises ValueError('no-base' / 'non-positive') so the caller can skip the row."""
    if mode == "clear":
        return None
    v = Decimal(str(value))
    if mode == "exact":
        n = v
    else:
        if base is None:
            raise ValueError("no-base")
        b = Decimal(str(base))
        n = b * (Decimal(1) + v / Decimal(100)) if mode == "pct" else b + v
    n = n.quantize(Decimal(1).scaleb(-int(round_to)), rounding=ROUND_HALF_UP)
    if n <= 0:
        raise ValueError("non-positive")
    return str(n)


# ------------------------------------------------------------------ Barsy rule management ---
def _pricelist_rows() -> list:
    idx = barsy.call("Pricelists_index", {"action_type": "values"}) or {}
    return (idx.get("rows", idx) if isinstance(idx, dict) else idx) or []


def _create_rule() -> int:
    """Create the persistent online price rule (type 1 = exact per-article), scope it to the online
    delivery types, activate it. Pricelists_edit doesn't return the new id here, so snapshot-diff the
    index (same trick as catalog.create_promo). NB: exact body validated against the live install by
    the Hermes session; all writes are gated until launch."""
    # Body validated live by the Hermes session. It's an EXACT-price rule (modificator_type 5): each
    # article's online price is driven by its per-article custom_price (§ Pricelists_save_articles),
    # since the panel computes the final price for every mode (exact/%/fixed) and writes it directly.
    # total_period + periods_date are REQUIRED (omitting either 500s); "1.1-31.12" = permanently on.
    values = {
        "pricelist_name": _RULE_NAME, "pricelist_type_id": 1, "pricelist_group_id": 1,
        "weight": 1, "periods_date": "1.1-31.12", "begin_price_type": 1,
        "price_tax_mode": 1, "price_tax_id": 2,
        "modificator_type": 5, "modificator_value": 0, "modificator_round_type": 0,
        "price_change_groups": [1], "total_period": "current", "price_type": 1,
    }
    before = {r.get("pricelist_id") for r in _pricelist_rows()}
    barsy.call("Pricelists_edit",
               {"action_type": "save", "active_struct_id": "eStructForm_1", "values": values})
    pid = None
    for r in _pricelist_rows():
        rid = r.get("pricelist_id")
        if rid is None or rid in before:
            continue
        if str(r.get("pricelist_name")) == _RULE_NAME:
            pid = rid if pid is None else max(pid, rid)
    if pid is None:
        raise RuntimeError("rule created but id unreadable")
    _apply_scope_and_activate(pid)
    return int(pid)


def _apply_scope_and_activate(pid: int) -> None:
    """SAFETY-CRITICAL: constrain the rule to the ONLINE delivery types only, then activate. A rule
    that lost its delivery-type scope would apply to in-store sales too and corrupt PHYSICAL prices —
    so we (re-)assert this on every write, never assuming it stuck."""
    tax_rows = [{"tax_region_id": str(t), "checked": 1 if t in _ONLINE_DELIVERY_TYPES else 0}
                for t in _ALL_DELIVERY_TYPES]
    barsy.call("Pricelists_save_tax_regions",
               {"id": pid, "action_type": "save", "values": {}, "rows": tax_rows})
    barsy.call("Pricelists_set_status", {"pricelist_id": pid, "status": 1})


def _find_rule_id() -> int | None:
    for r in _pricelist_rows():
        if str(r.get("pricelist_name")) == _RULE_NAME:
            rid = r.get("pricelist_id")
            return int(rid) if rid is not None else None
    return None


def _ensure_rule() -> int:
    if _RULE_ID_CACHE.get("id"):
        return _RULE_ID_CACHE["id"]
    pid = _find_rule_id()
    if pid is None:
        pid = _create_rule()
    _RULE_ID_CACHE["id"] = pid
    return pid


def _push_full_set(rows: list[tuple[int, str]]) -> dict:
    """Push the COMPLETE intended online set into the rule. Pricelists_save_articles overwrites the
    rule's article list (sibling Suppliers_save_articles is documented as ПРЕЗАПИСВА), so sending the
    full set is authoritative — omitting an article removes its online price (→ falls back to store
    price). Re-asserts the online-only scope first (physical-price safety)."""
    pid = _ensure_rule()
    _apply_scope_and_activate(pid)
    payload = [{"article_id": aid, "custom_price": cp, "req_amount": 1, "price_change_group": 1}
               for aid, cp in rows]
    barsy.call("Pricelists_save_articles", {"id": pid, "rows": payload})
    return {"rule_id": pid, "count": len(payload)}


# ------------------------------------------------------------------ API ---
class ApplyRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=_BULK_MAX)
    mode: str                        # exact | pct | fixed | clear
    value: float = 0.0
    round_to: int = 2
    confirm: bool = False


@router.get("")
async def list_online(session: AsyncSession = Depends(get_async_session),
                      actor: User = Depends(current_owner)):
    """Current online overrides (panel source of truth), joined with the product cache for name +
    store price so the UI can show store-vs-online side by side."""
    rows = (await session.execute(select(OnlinePrice))).scalars().all()
    ids = [r.article_id for r in rows]
    cache: dict[int, ProductCache] = {}
    if ids:
        for c in (await session.execute(
                select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all():
            cache[c.article_id] = c
    items = []
    for r in rows:
        pc = cache.get(r.article_id)
        items.append({"article_id": r.article_id, "online_price": r.custom_price, "mode": r.mode,
                      "name": pc.name if pc else "", "code": pc.code if pc else "",
                      "store_price": pc.price if pc else None, "updated_by": r.updated_by})
    items.sort(key=lambda x: str(x["name"] or ""))
    return {"items": items, "count": len(items), "rule_name": _RULE_NAME}


@router.post("/apply")
async def apply(req: ApplyRequest, actor: User = Depends(current_owner),
                session: AsyncSession = Depends(get_async_session)):
    """Set the ONLINE price on the selected articles by exact value / % of store price / fixed ±
    amount / clear. confirm=false → dry-run preview (writes nothing). With confirm + gate on: updates
    the panel's intended set and pushes the FULL set into the Barsy online rule. Owner-only, audited."""
    if req.mode not in _MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден режим (exact|pct|fixed|clear).")
    ids = list(dict.fromkeys(int(a) for a in req.article_ids))    # dedup, preserve order
    # store-price baselines: needed for pct/fixed math, and for the preview delta in every mode
    base = await run_in_threadpool(_bulk.price_baselines, ids)
    changes: dict[int, str | None] = {}
    preview: list[dict] = []
    uncovered: list[int] = []
    for aid in ids:
        try:
            newp = _compute(req.mode, req.value, base.get(aid), req.round_to)
        except ValueError:
            uncovered.append(aid)                 # relative op with no store price, or non-positive
            continue
        changes[aid] = newp
        preview.append({"article_id": aid, "store_price": base.get(aid), "online_price": newp})

    if not req.confirm:
        return {"dry_run": True, "mode": req.mode, "count": len(changes),
                "uncovered": uncovered, "preview": preview[:200]}
    if not barsy_write_live():
        return {"dry_run": True, "gated": True, "note": _GATED, "mode": req.mode,
                "count": len(changes), "uncovered": uncovered}

    # 1) update the panel's intended online set (source of truth)
    for aid, newp in changes.items():
        if newp is None:
            await session.execute(delete(OnlinePrice).where(OnlinePrice.article_id == aid))
        else:
            row = await session.get(OnlinePrice, aid)
            if row:
                row.custom_price, row.mode, row.updated_by = newp, req.mode, actor.email
            else:
                session.add(OnlinePrice(article_id=aid, custom_price=newp,
                                        mode=req.mode, updated_by=actor.email))
    await session.commit()

    # 2) push the COMPLETE set into the Barsy rule (overwrite semantics → authoritative)
    all_rows = (await session.execute(select(OnlinePrice))).scalars().all()
    try:
        res = await run_in_threadpool(
            _push_full_set, [(r.article_id, r.custom_price) for r in all_rows])
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy (онлайн цени): {str(e)[:180]}")
    await log_action(session, actor, "online_pricing.apply",
                     detail=f"mode={req.mode} changed={len(changes)} total={len(all_rows)} rule={res['rule_id']}")
    return {"dry_run": False, "mode": req.mode, "applied": len(changes), "uncovered": uncovered,
            "rule_id": res["rule_id"], "total_online": len(all_rows)}
