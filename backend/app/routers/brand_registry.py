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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import brands_registry as reg
from ..audit import log_action
from ..db import get_async_session
from ..models import BrandOverride, User
from ..users import current_active_user, current_owner

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
    rows = [_apply(b, ov.get(("brand", b["brand_id"])), ("canonical", "note"))
            for b in reg.brands()]
    return {"brands": rows, "count": len(rows)}


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
    row = (await session.execute(
        select(BrandOverride).where(BrandOverride.scope == req.scope,
                                    BrandOverride.ref == req.ref))).scalar_one_or_none()
    if row is None:
        row = BrandOverride(scope=req.scope, ref=req.ref)
        session.add(row)
    for f in ("asserts", "brand_id", "canonical", "role", "note"):
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
