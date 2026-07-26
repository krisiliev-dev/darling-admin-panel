# -*- coding: utf-8 -*-
"""Brands — the gazetteer picker + panel-owned brand store.

The canonical brand list lives in pipeline-v3/gazetteer.jsonl (107 confirmed brands) on the pipeline
machine. The deployed panel can't write that file (different host), so we BUNDLE a read-only snapshot
(app/data/brands/gazetteer.jsonl) for the autocomplete, and keep panel-added brands in admin.db
(Brand table). The picker merges both by name (case-insensitive). A future job syncs panel rows back
to the pipeline gazetteer via brand.upsert (idempotent). Row schema mirrors the gazetteer exactly:
{name, aliases[], logo, confirmed, source, ts} — absent fields on old rows default to []/null/false.
"""
import json
import os
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..db import get_async_session
from ..models import Brand, User
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/brands", tags=["brands"])

_GAZ = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "brands", "gazetteer.jsonl")


@lru_cache(maxsize=1)
def _bundled() -> list[dict]:
    """The bundled gazetteer snapshot, normalised to the full schema (defaults for old minimal rows)."""
    out = []
    try:
        with open(_GAZ, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("name"):
                    out.append({"name": str(r["name"]), "aliases": r.get("aliases") or [],
                                "logo": r.get("logo"), "confirmed": bool(r.get("confirmed", False)),
                                "source": r.get("source") or "scrape"})
    except OSError:
        pass
    return out


def _row(b: Brand) -> dict:
    return {"name": b.name, "aliases": json.loads(b.aliases_json or "[]"), "logo": b.logo or None,
            "confirmed": bool(b.confirmed), "source": b.source or "panel"}


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    aliases: list[str] = []
    logo: str = ""


@router.get("")
async def list_brands(q: str = "", limit: int = 20, user: User = Depends(current_active_user),
                      session: AsyncSession = Depends(get_async_session)):
    """Search brands (bundled snapshot ∪ panel store) by name or alias. Empty q → the whole list."""
    merged: dict[str, dict] = {}
    for b in _bundled():
        merged[b["name"].lower()] = b
    for br in (await session.execute(select(Brand))).scalars().all():
        merged[br.name.lower()] = _row(br)    # panel rows win (richer / edited)
    ql = q.strip().lower()
    items = []
    for b in merged.values():
        hay = " ".join([b["name"], *(b.get("aliases") or [])]).lower()
        if not ql or ql in hay:
            items.append(b)
    items.sort(key=lambda x: x["name"].lower())
    return {"brands": items[:max(1, min(1000, limit))],
            "known": ql in {b["name"].lower() for b in merged.values()} if ql else False}


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_brand(req: BrandIn, actor: User = Depends(require_roles("staff")),
                    session: AsyncSession = Depends(get_async_session)):
    """Add a new brand to the panel store (source=panel, confirmed=false). Idempotent by name."""
    name = req.name.strip()
    existing = (await session.execute(select(Brand).where(Brand.name == name))).scalar_one_or_none()
    if existing:
        return {"ok": True, "brand": _row(existing), "created": False}
    b = Brand(name=name, aliases_json=json.dumps([a.strip() for a in req.aliases if a.strip()], ensure_ascii=False),
              logo=req.logo.strip(), confirmed=False, source="panel")
    session.add(b)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Този бранд вече съществува.")
    await session.refresh(b)
    await log_action(session, actor, "brand.add", target=name)
    return {"ok": True, "brand": _row(b), "created": True}


class BrandEdit(BaseModel):
    aliases: list[str] = []
    logo: str = ""
    confirmed: bool = False


@router.put("/{name}")
async def upsert_brand(name: str, req: BrandEdit, actor: User = Depends(require_roles("staff")),
                       session: AsyncSession = Depends(get_async_session)):
    """Edit a brand (aliases/logo/confirmed). Editing a bundled gazetteer brand creates a panel
    OVERRIDE row with the same name (the deployed panel can't write the pipeline gazetteer)."""
    nm = name.strip()
    if not nm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празно име.")
    b = (await session.execute(select(Brand).where(Brand.name == nm))).scalar_one_or_none()
    if not b:
        b = Brand(name=nm, source="panel")
        session.add(b)
    b.aliases_json = json.dumps([a.strip() for a in req.aliases if a.strip()], ensure_ascii=False)
    b.logo = req.logo.strip()
    b.confirmed = bool(req.confirmed)
    await session.commit()
    await session.refresh(b)
    await log_action(session, actor, "brand.upsert", target=nm)
    return {"ok": True, "brand": _row(b)}


@router.delete("/{name}")
async def delete_brand(name: str, actor: User = Depends(require_roles("staff")),
                       session: AsyncSession = Depends(get_async_session)):
    """Remove a PANEL brand row (reverts to the bundled gazetteer entry if one exists). Bundled
    entries themselves aren't deleted here — they live in the pipeline gazetteer."""
    b = (await session.execute(select(Brand).where(Brand.name == name.strip()))).scalar_one_or_none()
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Няма такъв бранд в панел-хранилището (вградените се управляват в пайплайна).")
    await session.delete(b)
    await session.commit()
    await log_action(session, actor, "brand.delete", target=name.strip())
    return {"ok": True}
