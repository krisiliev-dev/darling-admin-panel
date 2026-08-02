# -*- coding: utf-8 -*-
"""Simplified brand registry (v2) — read endpoints. The panel's window onto the ONE flat
registry (brand -> canonical, aliases, prefixes, series). Editing is a later, separate
decision (persistence); this ships the READ side the owner asked for."""
from fastapi import APIRouter, Depends, HTTPException, status

from .. import brands_v2 as reg
from ..models import User
from ..users import current_active_user

router = APIRouter(prefix="/api/brands-v2", tags=["brands-v2"])


@router.get("/summary")
async def summary(_: User = Depends(current_active_user)):
    return reg.summary()


@router.get("")
async def brands(_: User = Depends(current_active_user)):
    rows = reg.brands()
    return {"brands": rows, "count": len(rows)}


@router.get("/{bid}")
async def brand(bid: str, _: User = Depends(current_active_user)):
    b = reg.brand(bid)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава марка в опростения регистър.")
    return b
