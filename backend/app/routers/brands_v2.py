# -*- coding: utf-8 -*-
"""Simplified brand registry (v2) — read + EDIT. The live JSON is the single source of
truth (brands_v2.py handles seed→live + atomic writes). Writes are owner-gated: a brand
edit changes how the whole catalogue resolves, so it's an owner decision like the old
override. Also serves the UNBRANDED products list so the owner can spot recovery patterns."""
import json
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import brands_v2 as reg
from ..audit import log_action
from ..db import get_async_session
from ..models import User
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/brands-v2", tags=["brands-v2"])
_DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "brands"


@router.get("/summary")
async def summary(_: User = Depends(current_active_user)):
    return reg.summary()


@router.get("")
async def brands(_: User = Depends(current_active_user)):
    rows = reg.brands()
    return {"brands": rows, "count": len(rows)}


@router.get("/unbranded")
async def unbranded(limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0),
                    q: str = Query(""), only_matched: bool = Query(False),
                    _: User = Depends(current_active_user)):
    """Sellable products with NO resolved brand — code, name, matched supplier, photo,
    supplier source, barcode-prefix family. The owner scans this to spot recovery angles."""
    path = _DATA / "unbranded_products.json"
    if not path.is_file():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Списъкът с продукти без марка още не е генериран "
                            "(pipeline-v3/build_unbranded_list.py).")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r.get("name") or "").lower()
                or ql in str(r.get("code") or "") or ql in (r.get("supplier") or "").lower()]
    if only_matched:
        rows = [r for r in rows if r.get("matched")]
    total = len(rows)
    return {"total": total, "returned": min(limit, max(0, total - offset)),
            "items": rows[offset:offset + limit]}


@router.get("/{bid}")
async def brand(bid: str, _: User = Depends(current_active_user)):
    b = reg.brand(bid)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка.")
    return b


class EditIn(BaseModel):
    canonical: str | None = Field(default=None, max_length=160)
    gs1_owner: str | None = None
    logo: str | None = None
    add: dict[str, list[str]] | None = None      # {aliases|prefixes|series: [values]}
    remove: dict[str, list[str]] | None = None


class CreateIn(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9a-zа-я\-]+$")
    canonical: str = Field(min_length=1, max_length=160)


@router.patch("/{bid}")
async def edit_brand(bid: str, req: EditIn, actor: User = Depends(current_owner),
                     session: AsyncSession = Depends(get_async_session)):
    try:
        row = reg.edit(bid, canonical=req.canonical, gs1_owner=req.gs1_owner,
                       logo=req.logo, add=req.add, remove=req.remove)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка.")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    await log_action(session, actor, "brand_v2.edit", target=bid,
                     detail=str(req.model_dump(exclude_none=True))[:200])
    return row


@router.post("")
async def create_brand(req: CreateIn, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    try:
        row = reg.create(req.id, req.canonical)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    await log_action(session, actor, "brand_v2.create", target=req.id)
    return row


@router.delete("/{bid}")
async def delete_brand(bid: str, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    try:
        reg.delete(bid)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка.")
    await log_action(session, actor, "brand_v2.delete", target=bid)
    return {"ok": True}
