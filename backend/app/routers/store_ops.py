# -*- coding: utf-8 -*-
"""Склад — store operations against Barsy: manual goods-load (Зареждане), write-off (Изписване),
transfer (Прехвърляне), document reversal (Сторно), plus reads (depots, write-off reasons, per-article
movement ledger / Движения). Ревизия (stocktake) lives in store_ops_revision once its post shape lands.

Barsy is the system of record; the panel just drives the documents. Every MUTATING call is behind the
STORE_OPS_BARSY_LIVE gate: OFF (default, pre-launch) => the endpoint DRY-RUNS — validates, writes nothing,
and returns the exact Barsy body it WOULD send (`dry_run: true`). This lets staff exercise the UI and lets
us confirm the (partly undocumented) Storeouts/Storemoves/Revisions wire shapes live before flipping on.

⚠ SHAPES: Storeloads_create + Storemoves_create are documented/verified. Storeouts_save's wire wrapper is
reverse-engineered (Hermes) and marked VERIFY-LIVE — trial it against a test object before STORE_OPS_BARSY_LIVE=1.
"""
import datetime as _dt
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import User
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/store-ops", tags=["store-ops"])


# ============================================================ helpers
def _today() -> str:
    return _dt.date.today().isoformat()


def _now() -> str:
    """Barsy doc_date format seen in the UI: 'YYYY-MM-DD HH:MM:SS'."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _amt(x: float) -> str:
    """Barsy wants quantities as strings; send '2' not '2.0' for whole numbers (matches the UI)."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _doc_num(prefix: str) -> str:
    """A unique-per-second doc number (Barsy requires a unique doc_num per document)."""
    return f"{prefix}-{int(time.time())}"


async def _depots() -> list[dict]:
    rows = await run_in_threadpool(barsy.call, "Depots_getlist", {"filters": {}})
    return rows or []


def _rows_payload(lines: list["OpLine"]) -> list[dict]:
    out = []
    for ln in lines:
        row: dict = {"article_id": ln.article_id, "amount": ln.amount}
        if ln.note:
            row["notes"] = ln.note
        out.append(row)
    return out


async def _gate_or_dry(actor: User, session: AsyncSession, *, action: str, method: str,
                       body: dict, target: str, warnings: list[str] | None = None) -> dict:
    """Standard store-ops write: dry-run (gate off) returns the body; live sends it + audits."""
    settings = get_settings()
    warnings = warnings or []
    if not settings.store_ops_barsy_live:
        await log_action(session, actor, f"{action}.dry_run", target=target,
                         detail=f"{method} (gate off)")
        return {"ok": True, "dry_run": True, "method": method, "barsy_body": body, "warnings": warnings}
    result = await run_in_threadpool(barsy.call, method, body)
    await log_action(session, actor, action, target=target, detail=str(result)[:200])
    return {"ok": True, "dry_run": False, "method": method, "result": result, "warnings": warnings}


# ============================================================ request models
class OpLine(BaseModel):
    article_id: int
    amount: float = Field(gt=0)
    note: str | None = None
    current_price: float | None = None   # load only (unit cost)


class LoadReq(BaseModel):
    """Manual Зареждане (goods-in without a formal delivery) — ad-hoc inflow / opening balance."""
    lines: list[OpLine] = Field(min_length=1)
    depot_id: int | None = None
    supplier_id: int | None = None
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


class WriteoffReq(BaseModel):
    """Изписване — remove stock (breakage/loss/own-use). reason_id from /writeoff-reasons."""
    lines: list[OpLine] = Field(min_length=1)
    reason_id: int
    depot_id: int | None = None
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


class TransferReq(BaseModel):
    """Прехвърляне — move stock depot→depot (needs ≥2 depots)."""
    lines: list[OpLine] = Field(min_length=1)
    depot_from: int
    depot_to: int
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


class StornoReq(BaseModel):
    """Сторно — reverse a posted storeload (goods-in) by booking the mirror return (operation_type 2)."""
    storeload_id: int
    reason: str = ""
    confirm: bool = False


# ============================================================ reads (never gated)
@router.get("/depots")
async def list_depots(_: User = Depends(current_active_user)):
    """Depots/warehouses. can_load / can_move_outside flags drive which ops are allowed per depot."""
    return {"depots": await _depots()}


@router.get("/writeoff-reasons")
async def writeoff_reasons(_: User = Depends(current_active_user)):
    """Write-off reasons for the Изписване dropdown. NOTE: Storeouts_getreasons (NOT
    Storeouts_reasons_getlist, which 500s). type_id -1 = Глобален (the only write-off type)."""
    try:
        rows = await run_in_threadpool(
            barsy.call, "Storeouts_getreasons", {"filters": {"delete_flag": 0, "type_id": -1}})
    except Exception:
        rows = []
    return {"reasons": rows or [], "type_id": -1}


@router.get("/movements")
async def movements(article_id: int | None = None, code: str | None = None, limit: int = 100,
                    _: User = Depends(current_active_user)):
    """Движения — a per-article stock-movement ledger merged from every document type that moves stock
    (loads, write-offs, transfers). Pure read = the audit trail 'why did stock change?'. Best-effort:
    each source is queried defensively so one unavailable method never blanks the whole ledger."""
    if not article_id and not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "article_id или code е задължителен.")
    flt = {"article_id": article_id} if article_id else {"code": code}
    sources = [
        ("load", "Storeloads_getlist"),
        ("writeoff", "Storeouts_movements_getlist"),
        ("transfer", "Storemoves_getlist"),
    ]
    entries: list[dict] = []
    errors: list[str] = []
    for kind, method in sources:
        try:
            rows = await run_in_threadpool(barsy.call, method, {"filters": flt}) or []
            for r in rows:
                entries.append({"kind": kind, "method": method, "doc": r})
        except Exception as e:  # noqa: BLE001 — one dead source must not blank the ledger
            errors.append(f"{method}: {str(e)[:60]}")
    return {"movements": entries[:limit], "count": len(entries), "errors": errors}


# ============================================================ writes (gated → dry-run)
@router.post("/load")
async def manual_load(req: LoadReq, actor: User = Depends(current_owner),
                      session: AsyncSession = Depends(get_async_session)):
    """Manual Зареждане → Storeloads_create operation_type 1 (documented + verified via delivery flow)."""
    s = get_settings()
    body = {
        "store_load": {
            "depot_id": req.depot_id or s.barsy_depot_id,
            "doc_date": req.doc_date or _today(),
            "doc_num": _doc_num("ZAR"),
            "operation_type": 1,                 # 1 = receipt/load
            "currency_id": s.barsy_currency_id,  # EUR
            "price_mode": s.barsy_price_mode,    # 1 = VAT-inclusive
            **({"supplier_id": req.supplier_id} if req.supplier_id else {}),
            **({"description": req.description} if req.description else {}),
        },
        "articles": [
            {"article_id": ln.article_id, "amount": ln.amount,
             "current_price": ln.current_price if ln.current_price is not None else 0}
            for ln in req.lines
        ],
        "action_type": "save_and_close",
    }
    return await _gate_or_dry(actor, session, action="storeop.load", method="Storeloads_create",
                              body=body, target=f"{len(req.lines)} реда")


@router.post("/writeoff")
async def write_off(req: WriteoffReq, actor: User = Depends(current_owner),
                    session: AsyncSession = Depends(get_async_session)):
    """Изписване → Storeouts_save. Envelope confirmed by capturing the Barsy UI's own save request
    (2026-07-27): `{action_type:"save", values:{…flat header incl depot_id STRING…}, rows:[…]}` — header
    is FLAT in `values`, `rows` is a sibling (NOT nested elements). All ids sent as strings.
    The reason_id drives the чл.79/80 ЗДДС VAT treatment — the panel records it; the протокол за брак is
    the audit defence. Reasons come from /writeoff-reasons."""
    depot = req.depot_id or get_settings().barsy_depot_id
    body = {
        # save_and_close POSTS the document → decrements stock (verified live: 10→9). Plain "save" is
        # a draft (status0) that does NOT move stock; "save_and_close" (status1) is the actual write-off.
        "action_type": "save_and_close",
        "values": {
            "depot_id": str(depot),
            "type_id": "-1",                     # Глобален (the only write-off type on this install)
            "doc_date": req.doc_date or _now(),
            "description": req.description,
            "deal_id": None,
        },
        "rows": [
            {"article_id": str(ln.article_id), "amount": _amt(ln.amount),
             "reason_id": str(req.reason_id), "notes": ln.note or "", "lot_value": ""}
            for ln in req.lines
        ],
    }
    return await _gate_or_dry(actor, session, action="storeop.writeoff", method="Storeouts_save",
                              body=body, target=f"{len(req.lines)} реда / reason {req.reason_id}")


@router.post("/transfer")
async def transfer(req: TransferReq, actor: User = Depends(current_owner),
                   session: AsyncSession = Depends(get_async_session)):
    """Прехвърляне → Storemoves_create (documented). Requires ≥2 depots — Barsy has only 'Основен' until a
    2nd is created, so we hard-check to give a clear message rather than a confusing Barsy error."""
    if req.depot_from == req.depot_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Складът-източник и складът-цел трябва да са различни.")
    depots = await _depots()
    if len({d.get("depot_id") for d in depots}) < 2:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Прехвърлянето изисква поне 2 склада в Barsy. В момента съществува само един ('Основен'). "
            "Създайте втори склад в Barsy, за да ползвате тази функция.")
    body = {
        "storemove": {
            "depot_id_left": req.depot_from,
            "depot_id_right": req.depot_to,
            "doc_date": req.doc_date or _today(),
            "description": req.description,
        },
        "rows": _rows_payload(req.lines),
        "action_type": "save_and_close",
    }
    return await _gate_or_dry(actor, session, action="storeop.transfer", method="Storemoves_create",
                              body=body, target=f"{req.depot_from}→{req.depot_to} / {len(req.lines)} реда")


@router.post("/storno")
async def storno(req: StornoReq, actor: User = Depends(current_owner),
                 session: AsyncSession = Depends(get_async_session)):
    """Сторно of a goods-in storeload → book the mirror return (Storeloads_create operation_type 2),
    restoring the prior stock/value. Posted documents are immutable; reversal is the only correction path.
    (Sale storno lives in the COD/orders flow via storno_of_account_id.)"""
    # read the original so we can mirror its rows as a return
    try:
        orig = await run_in_threadpool(barsy.call, "Storeloads_get", {"id": req.storeload_id})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Зареждането {req.storeload_id} не е намерено: {str(e)[:80]}")
    rows = (orig or {}).get("articles") or (orig or {}).get("rows") or []
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Оригиналното зареждане няма редове за сторниране.")
    s = get_settings()
    body = {
        "store_load": {
            "depot_id": (orig or {}).get("depot_id") or s.barsy_depot_id,
            "doc_date": _today(),
            "doc_num": _doc_num("STORNO"),
            "operation_type": 2,                 # 2 = return (reverse of a load)
            "currency_id": s.barsy_currency_id,
            "price_mode": s.barsy_price_mode,
            "description": f"Сторно на зареждане #{req.storeload_id}: {req.reason}".strip(),
        },
        "articles": [
            {"article_id": r.get("article_id"), "amount": r.get("amount"),
             "current_price": r.get("current_price") or 0}
            for r in rows if r.get("article_id")
        ],
        "action_type": "save_and_close",
    }
    return await _gate_or_dry(actor, session, action="storeop.storno", method="Storeloads_create",
                              body=body, target=f"storeload {req.storeload_id}")


# ============================================================ Ревизия (stocktake)
class RevisionLine(BaseModel):
    article_id: int
    counted: float = Field(ge=0)          # physically counted quantity (set-to-exact)


class RevisionReq(BaseModel):
    lines: list[RevisionLine] = Field(min_length=1)
    depot_id: int | None = None
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


async def _article_stock(article_id: int) -> float:
    """Current system on-hand for an article (best-effort; 0 on any read issue)."""
    try:
        r = await run_in_threadpool(barsy.call, "Articles_get",
                                    {"article_id": article_id, "extra_properties": {"store_amount": 1}})
        a = (r or {}).get("Articles_get") if isinstance(r, dict) else None
        a = a if isinstance(a, dict) else (r if isinstance(r, dict) else {})
        return float(a.get("store_amount") or 0)
    except Exception:  # noqa: BLE001 — a missing read must not break the count
        return 0.0


@router.post("/revision/preview")
async def revision_preview(req: RevisionReq, _: User = Depends(current_active_user)):
    """Variance per line for a stocktake: counted − system on-hand. READ-ONLY (no write) — the count
    sheet the operator reviews before posting. Negative = липса (shortage), positive = излишък (surplus)."""
    lines = []
    for ln in req.lines:
        sys_amt = await _article_stock(ln.article_id)
        lines.append({"article_id": ln.article_id, "system": sys_amt, "counted": ln.counted,
                      "variance": round(ln.counted - sys_amt, 3)})
    return {
        "lines": lines,
        "total_variance": round(sum(row["variance"] for row in lines), 3),
        "shortage_lines": sum(1 for row in lines if row["variance"] < 0),
        "surplus_lines": sum(1 for row in lines if row["variance"] > 0),
    }


@router.post("/revision")
async def revision_post(req: RevisionReq, actor: User = Depends(current_owner),
                        session: AsyncSession = Depends(get_async_session)):
    """Ревизия → `Revisions_save` with action_type save_and_close (posts the count = adjusts on-hand to
    the counted qty). Same flat eStructListForm envelope confirmed for Storeouts. Gated → dry-run body."""
    depot = req.depot_id or get_settings().barsy_depot_id
    body = {
        "action_type": "save_and_close",       # posts the revision (adjusts stock); "save" = draft
        "values": {"depot_id": str(depot), "doc_date": req.doc_date or _now(),
                   "description": req.description},
        "rows": [{"row_id": str(i + 1), "article_id": str(ln.article_id), "amount": _amt(ln.counted)}
                 for i, ln in enumerate(req.lines)],
    }
    return await _gate_or_dry(actor, session, action="storeop.revision", method="Revisions_save",
                              body=body, target=f"{len(req.lines)} реда / depot {depot}")
