# -*- coding: utf-8 -*-
"""Вратата на склада — the mounted-tablet kiosk at the warehouse door.

The owner's constraint shaped everything here: shop assistants serve a waiting client, carry
no device, and will not open an app. So the kiosk is a FIXTURE — a wall tablet, already on
this page, already logged in. Taking goods to the shopfloor is one wave past the scanner (or
one camera view); putting them back is the same with the direction flipped.

Two design rules, both deliberate:

  * THE KIOSK NEVER BLOCKS. An unknown barcode is recorded raw (code="") and the worker
    walks on — a dialog at the door defeats the door. Unknowns surface in the ledger for
    review instead.
  * PANEL-LOCAL LEDGER, single Barsy depot. A missed scan under
    a-client-is-waiting pressure is a WHEN, not an if — so this data must never gate ERP
    stock. Barsy keeps total stock (sales already decrement it at the каса); this ledger
    splits the total into warehouse vs shopfloor as a best-effort derived view, and ревизия
    corrects drift. The owner chose this kiosk over per-product capacity fields (2026-08-07).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..models import ProductCache, ShopfloorMove, User
from ..services import delivery_parse as DP
from ..users import current_active_user

router = APIRouter(prefix="/api/shopfloor", tags=["shopfloor"])

_DIRECTIONS = ("out", "back")          # out = към магазина · back = обратно в склада
_MAX_QTY = 500
_MAX_BATCH = 200


async def _resolve(session: AsyncSession, barcode: str) -> tuple[str, str]:
    """(our code, name) for a scanned barcode. ProductCache first (live catalogue mirror),
    then the supplier-map EAN index (same one the delivery scanner trusts). Best-effort by
    design — blanks are an answer, not an error."""
    row = (await session.execute(
        select(ProductCache).where(ProductCache.barcode == barcode).limit(1))).scalars().first()
    if row and (row.code or row.name):
        return row.code or "", row.name or ""
    our = DP.barcode_index()["ean_to_code"].get(barcode) or ""
    if our:
        byc = (await session.execute(
            select(ProductCache).where(ProductCache.code == our).limit(1))).scalars().first()
        return our, (byc.name if byc else "")
    return "", ""


def _move_out(m: ShopfloorMove) -> dict:
    return {
        "id": m.id, "barcode": m.barcode, "code": m.code, "name": m.name, "qty": m.qty,
        "direction": m.direction, "known": bool(m.code),
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _check_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна посока (out/back).")


class MoveReq(BaseModel):
    barcode: str
    qty: int = 1
    direction: str = "out"


@router.post("/move")
async def move(
    req: MoveReq,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """One wave past the scanner. Records the move and answers with what it recognised —
    the kiosk shows the product name as feedback, or an honest „непознат баркод, записан“."""
    bc = (req.barcode or "").strip()
    if not bc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празен баркод.")
    _check_direction(req.direction)
    if req.qty < 1 or req.qty > _MAX_QTY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Невалидно количество (1–{_MAX_QTY}).")
    code, name = await _resolve(session, bc)
    m = ShopfloorMove(barcode=bc, code=code, name=name, qty=req.qty,
                      direction=req.direction, created_by=actor.email)
    session.add(m)
    await session.commit()
    return {"ok": True, "move": _move_out(m)}


class MoveBatchReq(BaseModel):
    counts: dict[str, int]
    direction: str = "out"


@router.post("/move-batch")
async def move_batch(
    req: MoveBatchReq,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """The camera commit: several barcodes with counts, one direction, applied atomically —
    the same tally shape the delivery scanner produces."""
    _check_direction(req.direction)
    counts = {(k or "").strip(): v for k, v in req.counts.items() if (k or "").strip()}
    if not counts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празно сканиране.")
    if len(counts) > _MAX_BATCH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Твърде много кодове наведнъж (макс. {_MAX_BATCH}).")
    for bc, n in counts.items():
        if n < 1 or n > _MAX_QTY:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Невалиден брой за {bc}.")
    moves: list[ShopfloorMove] = []
    for bc, n in counts.items():
        code, name = await _resolve(session, bc)
        m = ShopfloorMove(barcode=bc, code=code, name=name, qty=n,
                          direction=req.direction, created_by=actor.email)
        session.add(m)
        moves.append(m)
    await session.commit()
    return {"ok": True, "moves": [_move_out(m) for m in moves]}


@router.get("/moves")
async def recent_moves(
    limit: int = 30,
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    rows = list((await session.execute(
        select(ShopfloorMove).order_by(ShopfloorMove.id.desc())
        .limit(max(1, min(limit, 200))))).scalars().all())
    return {"moves": [_move_out(m) for m in rows]}


@router.get("/onfloor")
async def on_floor(
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Net per product currently on the shopfloor by this ledger's account (out − back).
    A derived, best-effort view — sales are NOT subtracted here (the каса does not say which
    unit it sold), so this reads as „изнесено нетно“, not „налично в магазина“."""
    key = func.coalesce(func.nullif(ShopfloorMove.code, ""), ShopfloorMove.barcode).label("key")
    net = func.sum(case((ShopfloorMove.direction == "out", ShopfloorMove.qty),
                        else_=-ShopfloorMove.qty)).label("net")
    rows = (await session.execute(
        select(key, func.max(ShopfloorMove.name).label("name"),
               func.max(ShopfloorMove.barcode).label("barcode"), net)
        .group_by(key).having(net != 0).order_by(net.desc()))).all()
    return {"onfloor": [
        {"key": r.key, "name": r.name or "", "barcode": r.barcode or "", "net": int(r.net or 0)}
        for r in rows
    ]}
