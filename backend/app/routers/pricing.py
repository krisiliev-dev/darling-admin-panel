# -*- coding: utf-8 -*-
"""Ценообразуване — cost-plus-markup repricing (owner 2026-07-21).

The owner buys at a supplier COST and sells at a fixed markup (buy €1.00 +50% → €1.50). When the
supplier raises the cost to €1.50, the shelf price should move to €2.25 — but only the OWNER may
move a price. So: Barsy holds cost (`delivery_price`) + retail (`actual_price`); this module holds
the intended markup per article (panel-owned `pricing_rule`), detects when a cost change means the
price is now wrong, and PROPOSES the new retail. The owner reviews and applies; applying writes
`actual_price` back to Barsy. Nothing is auto-pushed — price is sensitive.

This refines the earlier 'retail = Microinvest продажна, never touched' note: repricing is now a
deliberate, owner-approved, markup-driven flow, not an AI edit.
"""
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy, barsy_write_live, db

_GATED = "Barsy запис е изключен (BARSY_WRITE_LIVE=0) — цената не е записана в ERP."
from ..db import get_async_session
from ..models import PricingGroupRule, PricingRule, User
from ..upstream import upstream
from ..users import current_active_user, current_owner, require_roles

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def _num(v):
    try:
        return round(float(v), 4) if v is not None else None
    except (TypeError, ValueError):
        return None


def _cost(row: dict):
    """Supplier cost to price from: the base delivery price, or the received-weighted average."""
    return _num(row.get("delivery_price")) or _num(row.get("avg_delivery_price"))


def _propose(cost: float, markup_pct: float) -> float:
    # Do the arithmetic in Decimal, THEN quantize half-UP. The old code multiplied in float first
    # (`cost * (1 + markup)`) and only wrapped the RESULT in Decimal — but that float can already be
    # a hair below an .xx5 boundary (e.g. 4.35×1.5 = 6.524999… not 6.525), so ROUND_HALF_UP rounded
    # the wrong way and shelf prices landed a cent low.
    val = (Decimal(str(cost)) * (Decimal("1") + Decimal(str(markup_pct)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(val)


_SUPPLIER_SQL = "json_extract(record_json,'$.fields.\"Доставчик\"')"


def _supplier_map() -> dict:
    """{barsy_code: supplier_name} from the pipeline DB (fast json_extract, no full-record parse)."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT code, {_SUPPLIER_SQL} FROM products").fetchall()
    except Exception:
        return {}
    finally:
        con.close()
    return {str(c): (s or "") for c, s in rows if c and s}


def _supplier_counts() -> list[dict]:
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT {_SUPPLIER_SQL} AS s, COUNT(*) FROM products GROUP BY s").fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [{"name": s, "product_count": c} for s, c in rows if s]


async def _group_rules(session, scope: str) -> dict:
    """{key: markup_pct} for a scope (e.g. supplier name → markup)."""
    rows = (await session.execute(
        select(PricingGroupRule).where(PricingGroupRule.scope == scope))).scalars().all()
    return {r.key: r.markup_pct for r in rows}


def _resolve_markup(article_rule, supplier: str, supplier_rules: dict):
    """Article override wins; else the supplier's group rule; else none. Returns (markup, source)."""
    if article_rule is not None:
        return article_rule.markup_pct, "product"
    if supplier and supplier in supplier_rules:
        return supplier_rules[supplier], "supplier"
    return None, None


@router.get("/review")
async def review(
    user: User = Depends(current_active_user),
    q: str = Query("", description="име или код"),
    only_flagged: bool = Query(False, description="само тези, чиято цена трябва да мръдне"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_async_session),
):
    """Cost vs retail vs intended markup, per article. `needs_apply` = the retail no longer matches
    cost×(1+markup) (usually because the supplier cost changed); `cost_changed` = cost moved since
    we last priced. Returns a worklist for the owner."""
    filters: dict = {}
    if q:
        filters["code" if q.strip().isdigit() else "article_name"] = q.strip()
    with upstream("Barsy каталог"):
        rows = await run_in_threadpool(barsy.call, "Articles_getlist", {"filters": filters, "length": limit})
    rules = {r.article_id: r for r in (await session.execute(select(PricingRule))).scalars().all()}
    supplier_rules = await _group_rules(session, "supplier")
    supplier_map = await run_in_threadpool(_supplier_map)

    out = []
    for a in (rows or []):
        aid = a.get("article_id")
        cost = _cost(a)
        retail = _num(a.get("actual_price"))
        if aid is None or cost is None or cost <= 0:
            continue  # can't compute a markup without a positive cost
        rule = rules.get(aid)
        supplier = supplier_map.get(str(a.get("code") or ""), "")
        target, source = _resolve_markup(rule, supplier, supplier_rules)  # product > supplier
        proposed = _propose(cost, target) if target is not None else None
        implied = round((retail - cost) / cost, 4) if (retail is not None) else None
        cost_changed = bool(rule and rule.last_cost is not None and abs(cost - rule.last_cost) > 0.005)
        needs_apply = bool(proposed is not None and retail is not None and abs(retail - proposed) > 0.01)
        item = {
            "article_id": aid, "code": a.get("code") or "", "name": a.get("article_name") or "",
            "supplier": supplier, "cost": round(cost, 2), "retail": round(retail, 2) if retail is not None else None,
            "implied_markup": implied, "target_markup": target, "markup_source": source,
            "proposed_retail": proposed, "cost_changed": cost_changed, "needs_apply": needs_apply,
        }
        if only_flagged and not (needs_apply or cost_changed):
            continue
        out.append(item)
    # surface the ones that need action first
    out.sort(key=lambda r: (not (r["needs_apply"] or r["cost_changed"]), r["name"]))
    return {"items": out}


class RuleBody(BaseModel):
    article_id: int
    markup_pct: float = Field(ge=0, le=10)  # 0.5 = +50%; cap at +1000% as a sanity bound


@router.post("/rule")
async def set_rule(
    body: RuleBody,
    actor: User = Depends(require_roles("staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Set the intended markup for an article. Records the current cost as the baseline we priced
    from, so a later cost change is detectable."""
    with upstream("Barsy артикул"):
        a = await run_in_threadpool(barsy.call, "Articles_get", {"article_id": body.article_id})
    if not isinstance(a, dict) or not a.get("article_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Артикулът не е намерен.")
    cost = _cost(a)
    rule = await session.get(PricingRule, body.article_id)
    if rule is None:
        rule = PricingRule(article_id=body.article_id)
        session.add(rule)
    rule.markup_pct = body.markup_pct
    rule.last_cost = cost
    rule.updated_by = actor.email
    await session.commit()
    await log_action(session, actor, "pricing.rule", target=str(body.article_id),
                     detail=f"markup={body.markup_pct}")
    proposed = _propose(cost, body.markup_pct) if cost else None
    return {"ok": True, "article_id": body.article_id, "markup_pct": body.markup_pct,
            "cost": round(cost, 2) if cost else None, "proposed_retail": proposed}


class ApplyBody(BaseModel):
    article_id: int
    confirm: bool = False


def _supplier_for(code) -> str:
    con = db.connect()
    try:
        row = con.execute(f"SELECT {_SUPPLIER_SQL} FROM products WHERE code=?", (str(code),)).fetchone()
    except Exception:
        return ""
    finally:
        con.close()
    return (row[0] if row else "") or ""


@router.post("/apply")
async def apply(
    body: ApplyBody,
    actor: User = Depends(current_owner),  # writing a price to Barsy is owner-only
    session: AsyncSession = Depends(get_async_session),
):
    """Apply the resolved markup (product rule → supplier rule): write actual_price = cost×(1+markup)
    to Barsy. Owner/manager-only + confirm-gated, because it changes a live shelf price in the SoR."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    with upstream("Barsy артикул"):
        a = await run_in_threadpool(barsy.call, "Articles_get", {"article_id": body.article_id})
    cost = _cost(a)
    if not cost or cost <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма доставна цена — не може да се преизчисли.")
    rule = await session.get(PricingRule, body.article_id)
    supplier = await run_in_threadpool(_supplier_for, a.get("code"))
    markup, source = _resolve_markup(rule, supplier, await _group_rules(session, "supplier"))
    if markup is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма зададена надценка (нито за продукта, нито за доставчика).")
    old_retail = _num(a.get("actual_price"))
    new_retail = _propose(cost, markup)
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED, "article_id": body.article_id,
                "old_retail": old_retail, "new_retail": new_retail, "markup_source": source}
    with upstream("Barsy обновяване"):
        await run_in_threadpool(barsy.call, "Articles_update",
                                {"article_id": body.article_id, "article": {"actual_price": new_retail}})
    if rule is not None:
        rule.last_cost = cost
        rule.updated_by = actor.email
        await session.commit()
    await log_action(session, actor, "pricing.apply", target=str(body.article_id),
                     detail=f"{old_retail} → {new_retail} (cost {cost} +{markup} via {source})")
    return {"ok": True, "article_id": body.article_id, "old_retail": old_retail,
            "new_retail": new_retail, "markup_source": source}


# ------------------------------------------------------- supplier / group rules (managers) ---
@router.get("/suppliers")
async def suppliers(
    actor: User = Depends(current_owner),  # bulk pricing is a manager/owner surface
    session: AsyncSession = Depends(get_async_session),
):
    """Every supplier (from the pipeline DB) with its product count + the markup rule set for it."""
    rules = await _group_rules(session, "supplier")
    counts = await run_in_threadpool(_supplier_counts)
    for c in counts:
        c["markup_pct"] = rules.get(c["name"])
    counts.sort(key=lambda c: c["product_count"], reverse=True)
    return {"suppliers": counts}


class GroupBody(BaseModel):
    scope: str = Field(pattern="^(supplier|category)$")
    key: str = Field(min_length=1, max_length=255)
    markup_pct: float = Field(ge=0, le=10)


@router.post("/group")
async def set_group(
    body: GroupBody,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Set a markup rule for a whole supplier (or category). Doesn't touch prices — the manager then
    applies it in bulk. Products with their own per-article rule are unaffected (product wins)."""
    row = (await session.execute(select(PricingGroupRule).where(
        PricingGroupRule.scope == body.scope, PricingGroupRule.key == body.key))).scalar_one_or_none()
    if row is None:
        row = PricingGroupRule(scope=body.scope, key=body.key)
        session.add(row)
    row.markup_pct = body.markup_pct
    row.updated_by = actor.email
    await session.commit()
    await log_action(session, actor, "pricing.group", target=f"{body.scope}:{body.key}",
                     detail=f"markup={body.markup_pct}")
    return {"ok": True, "scope": body.scope, "key": body.key, "markup_pct": body.markup_pct}


class ApplySupplierBody(BaseModel):
    supplier: str = Field(min_length=1, max_length=255)
    confirm: bool = False
    limit: int = Field(2000, ge=1, le=5000)


@router.post("/apply-supplier")
async def apply_supplier(
    body: ApplySupplierBody,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """BULK: recompute + write retail for every product of a supplier that needs it, using the
    resolved markup (a product's own rule still wins). Owner/manager-only + confirm. Skips products
    already in line, or without a cost. Returns applied/skipped counts."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    supplier_rules = await _group_rules(session, "supplier")
    if body.supplier not in supplier_rules:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма зададена надценка за този доставчик.")
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED, "applied": 0, "skipped": 0}
    supplier_map = await run_in_threadpool(_supplier_map)
    codes = {c for c, s in supplier_map.items() if s == body.supplier}
    if not codes:
        return {"ok": True, "applied": 0, "skipped": 0, "reason": "няма продукти"}
    rules = {r.article_id: r for r in (await session.execute(select(PricingRule))).scalars().all()}
    with upstream("Barsy каталог"):
        rows = await run_in_threadpool(barsy.call, "Articles_getlist", {"filters": {}, "length": body.limit})

    # M1: products of this supplier BEYOND the fetched window are neither repriced nor visible — count
    # them so the operator isn't told the whole supplier is done when it isn't.
    seen_codes = {str(a.get("code") or "") for a in (rows or [])} & codes
    uncovered = len(codes) - len(seen_codes)

    applied = skipped = 0
    for a in (rows or []):
        if str(a.get("code") or "") not in codes:
            continue
        aid, cost, retail = a.get("article_id"), _cost(a), _num(a.get("actual_price"))
        if aid is None or not cost or cost <= 0:
            skipped += 1
            continue
        markup, _src = _resolve_markup(rules.get(aid), body.supplier, supplier_rules)
        if markup is None:
            skipped += 1
            continue
        new_retail = _propose(cost, markup)
        if retail is not None and abs(retail - new_retail) <= 0.01:
            skipped += 1  # already in line
            continue
        try:
            await run_in_threadpool(barsy.call, "Articles_update",
                                    {"article_id": aid, "article": {"actual_price": new_retail}})
        except Exception:
            skipped += 1
            continue
        r = rules.get(aid)
        if r is not None:
            r.last_cost = cost
        applied += 1
    await session.commit()
    skipped += uncovered
    await log_action(session, actor, "pricing.apply_supplier", target=body.supplier,
                     detail=f"applied={applied} skipped={skipped} uncovered={uncovered}")
    return {"ok": True, "supplier": body.supplier, "total": len(codes),
            "applied": applied, "skipped": skipped, "uncovered": uncovered,
            "complete": uncovered == 0}
