# -*- coding: utf-8 -*-
"""Марки (регистър) — display and hand-correct the brand registry (#99).

The registry is machine-derived and READ-ONLY here; every human correction becomes a `BrandOverride`
row that wins at resolution. A pipeline regeneration therefore cannot discard a judgement — the
failure already seen with `gazetteer.jsonl`.

Two properties this surface exists to preserve:
  • `asserts` is the ONLY gate. A suggestion (brand_id without asserts) is shown as a suggestion WITH
    its evidence, never as the product's brand. 465 prefixes are in that state awaiting the owner.
  • Every write states its BLAST RADIUS first. Turning `asserts` off on a prefix unbrands every
    product under it, and that number belongs in front of the person clicking, not in a report after.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import brands_registry as reg
from ..audit import log_action
from ..core import db as core_db
from ..db import get_async_session
from ..models import BrandOverride, User
from ..users import current_active_user, current_owner

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/brand-registry", tags=["brand-registry"])


async def _overrides(session: AsyncSession) -> dict[tuple[str, str], BrandOverride]:
    rows = (await session.execute(select(BrandOverride))).scalars().all()
    return {(r.scope, r.ref): r for r in rows}


def _apply(row: dict, ov: BrandOverride | None, fields: tuple[str, ...]) -> dict:
    """Overlay a human correction. Only fields actually SET are applied — an override says what is
    wrong, not the whole record — and `overridden` names them so the UI can show what a human decided
    rather than presenting machine and human values as one indistinguishable blob."""
    if ov is None:
        return {**row, "overridden": []}
    changed = []
    out = dict(row)
    for f in fields:
        v = getattr(ov, f, None)
        if v is not None:
            out[f] = v
            changed.append(f)
    if ov.aliases_json is not None and "aliases" in row:
        try:
            out["aliases"] = json.loads(ov.aliases_json)
            changed.append("aliases")
        except ValueError:
            pass
    return {**out, "overridden": changed,
            "overridden_by": ov.actor, "overridden_why": ov.why,
            "overridden_at": ov.updated_at.isoformat() if ov.updated_at else ""}


@router.get("/summary")
async def summary(_: User = Depends(current_active_user)):
    """Headline counts. `products_under_asserting_prefixes` is NOT the sum of the per-prefix column —
    products with barcodes under several prefixes are counted under each, so summing overstates it."""
    return reg.summary()


@router.get("/brands")
async def brands(_: User = Depends(current_active_user),
                 session: AsyncSession = Depends(get_async_session)):
    ov = await _overrides(session)
    rows = [_apply(b, ov.get(("brand", b["brand_id"])), ("canonical", "note", "confirmed", "kind"))
            for b in reg.brands()]
    return {"brands": rows, "count": len(rows)}


@router.get("/brand/{brand_id}")
async def brand_detail(brand_id: str, _: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    """One brand, fully: its registry fields (⊕ the human override, labelled), every prefix that
    names it — asserting and suggestions alike — and the manufacturers behind them. The edit screen
    is built on this, so everything carries enough context to decide, not just to display."""
    d = reg.brand_detail(brand_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка в регистъра.")
    ov = await _overrides(session)
    out = _apply(d, ov.get(("brand", brand_id)), ("canonical", "note", "confirmed", "kind"))
    out["prefixes"] = [_apply(r, ov.get(("prefix", r["prefix"])), ("brand_id", "asserts"))
                       for r in d["prefixes"]]
    return out


@router.get("/brand/{brand_id}/products")
async def brand_products(brand_id: str, limit: int = Query(300, ge=1, le=2000),
                         _: User = Depends(current_active_user),
                         session: AsyncSession = Depends(get_async_session)):
    """The products this brand labels TODAY, i.e. under its effectively-asserting prefixes
    (registry ⊕ overrides). Read-only here by design — products are edited on their own page.

    Fails LOUD (503) when the code→prefix snapshot is missing: an empty list would claim
    "this brand has no products", which is a different fact entirely."""
    r = reg.load()
    if brand_id not in (r.get("brands") or {}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка в регистъра.")
    cmap = reg.codes_map()
    if cmap is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Липсва снимката код→префикс (code_prefixes.json). Генерирай я с "
            "pipeline-v3/export_panel_snapshots.py и деплойни — без нея списъкът "
            "би излъгал, че марката няма продукти.")
    ov = await _overrides(session)

    effective: set[str] = set()
    for p, v in (r.get("prefixes") or {}).items():
        o = ov.get(("prefix", p))
        bid = (o.brand_id if (o and o.brand_id is not None) else v.get("brand_id"))
        if bid != brand_id:
            continue
        asserts = (o.asserts if (o and o.asserts is not None) else bool(v.get("asserts")))
        if asserts:
            effective.add(p)

    codes = sorted(c for c, plist in cmap.items() if any(p in effective for p in plist))
    total, codes = len(codes), codes[:limit]
    items, catalog_available = _product_cards(codes)
    return {"brand_id": brand_id, "total": total, "returned": len(codes),
            "asserting_prefixes": sorted(effective), "items": items,
            "catalog_available": catalog_available, "snapshot": reg.codes_meta()}


_ALIAS_CACHE: dict = {"ts": 0.0, "tasks": {}, "products": {}}


def _intkey(s: str) -> str:
    s = s.strip()
    return str(int(s)) if s.isdigit() else s


def _alias_maps(con) -> tuple[dict, dict]:
    """int-value -> the table's OWN spelling of a code, per table, cached ~10 min.

    The catalogue's code spellings are inconsistent about leading zeros ACROSS SOURCES
    ('131680' in tasks vs '0131680' in barcodes.jsonl, '58' vs '058'), so an exact-string
    join silently loses thousands of legitimate matches. Same-value aliasing recovers
    them without guessing padding widths."""
    import time as _time
    if _time.monotonic() - _ALIAS_CACHE["ts"] < 600 and _ALIAS_CACHE["tasks"]:
        return _ALIAS_CACHE["tasks"], _ALIAS_CACHE["products"]
    t_map: dict = {}
    p_map: dict = {}
    for (c,) in con.execute("SELECT code FROM tasks"):
        s = str(c)
        t_map.setdefault(_intkey(s), s)
    for (c,) in con.execute("SELECT code FROM products"):
        s = str(c)
        p_map.setdefault(_intkey(s), s)
    _ALIAS_CACHE.update(ts=_time.monotonic(), tasks=t_map, products=p_map)
    return t_map, p_map


def _product_cards(codes: list[str]) -> tuple[list[dict], bool]:
    """code → {code, name, photo, enriched} for the codes the panel knows (the v2-processed
    subset); unknown codes still return as code-only cards — a product outside pipeline.db is
    still the brand's product, and hiding it would undercount.

    Returns (cards, catalog_available). The CODE LIST is the fact here (it comes from the
    snapshot); names/photos are display garnish from the catalogue DB. So an unreachable or
    empty catalogue degrades to code-only cards with `catalog_available=False` — three states,
    never a 500, and never "unenriched" standing in for "we could not look". (CI has no
    catalogue at all, and sqlite invents an EMPTY db for a missing file rather than erroring —
    the exact pathology in the handoffs — so this path is not theoretical.)"""
    known: dict[str, dict] = {}
    enriched_codes: set[str] = set()
    catalog_available = True
    if codes:
        try:
            con = core_db.connect()
            try:
                t_map, p_map = _alias_maps(con)
                # resolve each requested code to each table's own spelling (leading-zero chaos)
                prod_spell = {c: p_map.get(_intkey(c), c) for c in codes}
                task_spell = {c: t_map.get(_intkey(c), c) for c in codes}
                back_prod = {v: k for k, v in prod_spell.items()}
                back_task = {v: k for k, v in task_spell.items()}
                want = list(prod_spell.values())
                for i in range(0, len(want), 400):
                    chunk = want[i:i + 400]
                    q = ",".join("?" * len(chunk))
                    for dbcode, fj in con.execute(
                            f"SELECT code, fields_json FROM products WHERE code IN ({q})", chunk):
                        code = back_prod.get(str(dbcode), str(dbcode))
                        try:
                            f = json.loads(fj or "{}")
                        except ValueError:
                            f = {}
                        known[code] = {"name": f.get("Наименование"),
                                       "photo": _first_url(f.get("Снимки"))}
                        enriched_codes.add(code)
                # fallback: the ORIGINALS — raw Microinvest name + the supplier's own photo URL
                # (tasks table). For judging "what IS this product" during review the original is
                # exactly right; coverage is near-total (14,255/14,292 names, 13,225 photo URLs)
                # versus the enriched subset.
                missing = [task_spell[c] for c in codes
                           if not (known.get(c) or {}).get("name")
                           or not (known.get(c) or {}).get("photo")]
                for i in range(0, len(missing), 300):
                    chunk = missing[i:i + 300]
                    q = ",".join("?" * len(chunk))
                    for dbcode, tj in con.execute(
                            f"SELECT code, task_json FROM tasks WHERE code IN ({q})", chunk):
                        code = back_task.get(str(dbcode), str(dbcode))
                        try:
                            t = json.loads(tj or "{}")
                        except ValueError:
                            t = {}
                        gt = t.get("ground_truth") or {}
                        raw_name = ((t.get("product") or {}).get("Наименование") or "").strip()
                        supplier_name = (gt.get("BG име (доставчик)") or "").strip()
                        row = known.setdefault(code, {"name": None, "photo": None})
                        row["name"] = row["name"] or raw_name or supplier_name or None
                        row["photo"] = row["photo"] or _first_url(gt.get("Снимка"))
            finally:
                con.close()
        except Exception:  # noqa: BLE001 — sqlite3.Error, db.connect refusal, missing table
            log.warning("catalogue unavailable for product cards — returning code-only cards",
                        exc_info=True)
            known, enriched_codes, catalog_available = {}, set(), False
    cards = [{"code": c,
              "name": (known.get(c) or {}).get("name"),
              "photo": (known.get(c) or {}).get("photo"),
              "enriched": c in enriched_codes} for c in codes]
    # review UX: photographed products first, then named, then bare codes
    cards.sort(key=lambda i: (i["photo"] is None, i["name"] is None, i["code"]))
    return (cards, catalog_available)


def _first_url(v) -> str | None:
    """First http(s) URL out of a possibly comma-separated string field; None otherwise."""
    if not isinstance(v, str):
        return None
    first = v.split(",")[0].strip()
    return first if first.startswith("http") else None


@router.get("/prefix/{prefix}/products")
async def prefix_products(prefix: str, limit: int = Query(60, ge=1, le=500),
                          _: User = Depends(current_active_user)):
    """The products a PREFIX decision affects — the review queue's expandable evidence. Read-only.
    Same loud-failure rule as the brand list: a missing snapshot is a 503, never an empty 200."""
    r = reg.load()
    if prefix not in (r.get("prefixes") or {}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такъв префикс в регистъра.")
    cmap = reg.codes_map()
    if cmap is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Липсва снимката код→префикс (code_prefixes.json). Генерирай я с "
            "pipeline-v3/export_panel_snapshots.py и деплойни — без нея списъкът "
            "би излъгал, че префиксът не засяга продукти.")
    codes = sorted(c for c, plist in cmap.items() if prefix in plist)
    total, codes = len(codes), codes[:limit]
    items, catalog_available = _product_cards(codes)
    return {"prefix": prefix, "total": total, "returned": len(codes),
            "items": items, "catalog_available": catalog_available,
            "snapshot": reg.codes_meta()}


@router.get("/review")
async def review(limit: int = Query(200, ge=1, le=2000),
                 _: User = Depends(current_active_user),
                 session: AsyncSession = Depends(get_async_session)):
    """The owner's hand-review queue: prefixes carrying a machine SUGGESTION that does not yet brand
    anything. Ordered by how many products ride on the decision."""
    ov = await _overrides(session)
    rows = []
    for r in reg.review_queue(limit=limit):
        rows.append(_apply(r, ov.get(("prefix", r["prefix"])), ("brand_id", "asserts")))
    return {"queue": rows, "count": len(rows)}


class OverrideIn(BaseModel):
    scope: str = Field(pattern="^(prefix|brand)$")
    ref: str = Field(min_length=1, max_length=64)
    why: str = Field(min_length=3, max_length=2000)   # required: an unattributable re-brand is unreviewable
    asserts: bool | None = None
    brand_id: str | None = Field(default=None, max_length=64)
    canonical: str | None = Field(default=None, max_length=160)
    aliases: list[str] | None = None
    role: str | None = Field(default=None, max_length=32)
    note: str | None = None
    confirmed: bool | None = None
    kind: str | None = Field(default=None, max_length=32)


@router.get("/blast-radius")
async def blast_radius(scope: str = Query(pattern="^(prefix|brand)$"), ref: str = Query(min_length=1),
                       _: User = Depends(current_active_user)):
    """How many products an edit to this target would affect — fetched BEFORE the edit so the number
    is in front of the person deciding. The same click with and without it is a different decision."""
    r = reg.load()
    if scope == "prefix":
        p = (r.get("prefixes") or {}).get(ref)
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такъв префикс в регистъра.")
        return {"scope": scope, "ref": ref, "product_count": p.get("product_count") or 0,
                "asserts": bool(p.get("asserts"))}
    match = [b for b in reg.brands() if b["brand_id"] == ref]
    if not match:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка в регистъра.")
    return {"scope": scope, "ref": ref, "product_count": match[0]["product_count"],
            "asserting_prefixes": match[0]["asserting_prefixes"]}


@router.post("/override")
async def set_override(req: OverrideIn, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    """Record a human correction. OWNER-gated: one `asserts` flip can re-brand thousands of products,
    which is a business decision about how the catalogue reads, not a system-administration one."""
    if req.scope == "prefix" and req.ref not in (reg.load().get("prefixes") or {}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такъв префикс в регистъра.")
    if req.scope == "brand" and req.ref not in (reg.load().get("brands") or {}):
        # same reasoning as the prefix check: an override against a target that does not exist
        # would sit forever, silently applying to nothing
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка в регистъра.")
    row = (await session.execute(
        select(BrandOverride).where(BrandOverride.scope == req.scope,
                                    BrandOverride.ref == req.ref))).scalar_one_or_none()
    if row is None:
        row = BrandOverride(scope=req.scope, ref=req.ref)
        session.add(row)
    for f in ("asserts", "brand_id", "canonical", "role", "note", "confirmed", "kind"):
        v = getattr(req, f)
        if v is not None:
            setattr(row, f, v)
    if req.aliases is not None:
        row.aliases_json = json.dumps(req.aliases, ensure_ascii=False)
    row.why, row.actor = req.why, actor.email
    await session.commit()
    await log_action(session, actor, "brand.override", target=f"{req.scope}:{req.ref}",
                     detail=req.why[:200])
    return {"ok": True, "scope": req.scope, "ref": req.ref}


@router.delete("/override")
async def clear_override(scope: str = Query(pattern="^(prefix|brand)$"), ref: str = Query(min_length=1),
                         actor: User = Depends(current_owner),
                         session: AsyncSession = Depends(get_async_session)):
    """Drop a correction and fall back to the machine value — the undo for a decision made in error."""
    row = (await session.execute(
        select(BrandOverride).where(BrandOverride.scope == scope,
                                    BrandOverride.ref == ref))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма ръчна корекция за този запис.")
    await session.delete(row)
    await session.commit()
    await log_action(session, actor, "brand.override.clear", target=f"{scope}:{ref}")
    return {"ok": True}
