# -*- coding: utf-8 -*-
"""Склад — store operations against Barsy: manual goods-load (Зареждане), write-off (Изписване),
transfer (Прехвърляне), document reversal (Сторно), plus reads (depots, write-off reasons, per-article
movement ledger / Движения). Ревизия (stocktake) lives in store_ops_revision once its post shape lands.

Barsy is the system of record; the panel just drives the documents. Every MUTATING call is behind the
STORE_OPS_BARSY_LIVE gate: OFF (default, pre-launch) => the endpoint DRY-RUNS — validates, writes nothing,
and returns the exact Barsy body it WOULD send (`dry_run: true`). This lets staff exercise the UI and lets
us confirm the (partly undocumented) Storeouts/Storemoves/Revisions wire shapes live before flipping on.

⚠ SHAPES: Storeloads_create + Storemoves_create are documented/verified. Storeouts_save and Revisions_save
were CAPTURED from Barsy's own admin UI (owner clicked «Запиши» with an XHR interceptor armed, 2026-07-28)
— those two are now real shapes, not reverse-engineering. Storemoves_save (Прехвърляне's UI path) and the
revision CLOSE/apply step are still uncaptured; see the notes at each.
"""
import asyncio
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


async def _article(article_id: int) -> dict:
    """One article's card, or {} if unreadable. Barsy's own UI calls Articles_get when a row is picked in
    a store document — the denormalised fields it then sends (name, unit, price, lot type) come from here."""
    try:
        a = await run_in_threadpool(barsy.call, "Articles_get", {"article_id": article_id})
        return a if isinstance(a, dict) else {}
    except Exception:  # noqa: BLE001 — a missing card must not block the document
        return {}


async def _article_names(ids: list[int]) -> dict[int, str]:
    """article_id → article_name for the rows of a document. Barsy stores by id but its UI sends the name
    ALONGSIDE it (denormalised, e.g. in a Storeouts row) and that name is what a human later reads on the
    протокол за брак — so we resolve real names rather than shipping blanks. Bounded + best-effort."""
    sem = asyncio.Semaphore(8)

    async def _one(aid: int) -> tuple[int, str]:
        async with sem:
            return aid, str((await _article(aid)).get("article_name") or "")

    return dict(await asyncio.gather(*[_one(i) for i in dict.fromkeys(ids)]))


def _rows_payload(lines: list["OpLine"]) -> list[dict]:
    out = []
    for ln in lines:
        row: dict = {"article_id": ln.article_id, "amount": ln.amount}
        if ln.note:
            row["notes"] = ln.note
        out.append(row)
    return out


async def _gate_or_dry(actor: User, session: AsyncSession, *, action: str, method: str,
                       body: dict, target: str, confirm: bool = False,
                       warnings: list[str] | None = None) -> dict:
    """Standard store-ops write. Gate OFF => dry-run: return the exact body, write nothing (confirm not
    needed to preview). Gate ON => a REAL Barsy write, so require explicit `confirm` (matches the
    intake/delivery pattern — a stray call can't mutate stock) + audit."""
    settings = get_settings()
    warnings = warnings or []
    if not settings.store_ops_barsy_live:
        await log_action(session, actor, f"{action}.dry_run", target=target,
                         detail=f"{method} (gate off)")
        return {"ok": True, "dry_run": True, "method": method, "barsy_body": body, "warnings": warnings}
    if not confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Реалният запис изисква потвърждение (confirm=true).")
    result = await run_in_threadpool(barsy.call, method, body)
    await log_action(session, actor, action, target=target, detail=str(result)[:200])
    return {"ok": True, "dry_run": False, "method": method, "result": result, "warnings": warnings}


# ============================================================ request models
class OpLine(BaseModel):
    article_id: int
    amount: float = Field(gt=0)
    note: str | None = None
    current_price: float | None = None   # load only (unit cost)
    # Barsy's UI sends the article NAME denormalised beside the id in a store-document row. The picker
    # already knows it, so let the client pass it and save a lookup; the backend resolves any that are
    # missing rather than sending a blank into a document a human will later read.
    article_name: str | None = None
    # Изписване only. Barsy models the write-off reason PER ROW, and that is not cosmetic: „Липса"
    # (shortage) and „Собствена покупка" (own use) carry DIFFERENT чл.79/80 ЗДДС treatment. One
    # document may legitimately mix them, so a single document-level reason would mistag lines.
    reason_id: int | None = None


class LoadReq(BaseModel):
    """Manual Зареждане (goods-in without a formal delivery) — ad-hoc inflow / opening balance."""
    lines: list[OpLine] = Field(min_length=1)
    depot_id: int | None = None
    supplier_id: int | None = None
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


class WriteoffReq(BaseModel):
    """Изписване — remove stock (breakage/loss/own-use). reason_id from /writeoff-reasons.
    `reason_id` here is only the DEFAULT for lines that don't carry their own — Barsy stores the
    reason per row (different VAT treatment per reason), so the per-line value always wins."""
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
    """Сторно — reverse a goods-in by booking the mirror return (Storeloads_create operation_type 2).
    Barsy has no Storeloads_get to read a doc's rows back, so the caller passes the lines to reverse
    (the panel knows them from the delivery it's reversing); storeload_id is an optional reference."""
    lines: list[OpLine] = Field(min_length=1)
    storeload_id: int | None = None
    depot_id: int | None = None
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


@router.get("/availability")
async def availability(article_id: int, _: User = Depends(current_active_user)):
    """Наличност of one article across ALL depots — what the Прехвърляне screen needs, since a transfer
    has two sides and moving stock you don't have is the mistake worth preventing at the form.

    Uses `Articles_getAvailability {article_id, depots:[…]}` (undocumented; it is what Barsy's own UI
    calls for the multi-depot view). Falls back to per-depot `Articles_getStoreAmount` reads so the column
    still fills if that method is unavailable — and a depot whose read FAILED reports null, never 0."""
    depots = await _depots()
    ids = [int(d["depot_id"]) for d in depots if d.get("depot_id") is not None]
    names = {int(d["depot_id"]): (d.get("depot_name") or str(d["depot_id"]))
             for d in depots if d.get("depot_id") is not None}
    try:
        raw = await run_in_threadpool(barsy.call, "Articles_getAvailability",
                                      {"article_id": article_id, "depots": ids})
    except Exception:  # noqa: BLE001 — fall back to the single-depot reads below
        raw = None
    rows: list[dict] = []
    if isinstance(raw, list) and raw:
        for r in raw:
            did = int(r.get("depot_id") or 0)
            rows.append({"depot_id": did, "depot_name": names.get(did, str(did)), "amount": _num(r)})
    else:
        amounts = await asyncio.gather(*[_article_stock(article_id, d) for d in ids])
        rows = [{"depot_id": d, "depot_name": names.get(d, str(d)), "amount": a}
                for d, a in zip(ids, amounts)]
    return {"article_id": article_id, "depots": rows}


@router.get("/movements")
async def movements(article_id: int | None = None, code: str | None = None, limit: int = 100,
                    _: User = Depends(current_active_user)):
    """Движения — a per-article stock-movement ledger merged from every document type that moves stock
    (loads, write-offs, transfers). Pure read = the audit trail 'why did stock change?'. Best-effort:
    each source is queried defensively so one unavailable method never blanks the whole ledger."""
    if not article_id and not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "article_id или code е задължителен.")
    flt = {"article_id": article_id} if article_id else {"code": code}
    # Documents that move stock and expose a per-article getlist. (Write-offs are NOT here: Barsy's
    # Storeouts_movements takes a store-out id, not an article filter — so brак isn't in this ledger.)
    sources = [("load", "Storeloads_getlist"), ("transfer", "Storemoves_getlist")]
    entries: list[dict] = []
    errors: list[str] = []
    for kind, method in sources:
        try:
            rows = await run_in_threadpool(barsy.call, method, {"filters": flt}) or []
            for r in rows:
                entries.append({"kind": kind, "method": method,
                                "doc_date": r.get("doc_date") or r.get("date") or "", "doc": r})
        except Exception as e:  # noqa: BLE001 — one dead source must not blank the ledger
            errors.append(f"{method}: {str(e)[:60]}")
    entries.sort(key=lambda e: e["doc_date"], reverse=True)   # newest first
    shown = entries[:limit]
    return {"movements": shown, "count": len(shown), "total": len(entries),
            "truncated": len(entries) > limit,
            "note": "Изписвания (брак) не са включени — Storeouts_movements е по документ, не по артикул.",
            "errors": errors}


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
            # strings, matching the verified delivery-path shape (intake_delivery._storeload_payload)
            {"article_id": str(ln.article_id), "amount": _amt(ln.amount),
             "current_price": str(ln.current_price if ln.current_price is not None else 0)}
            for ln in req.lines
        ],
        "action_type": "save_and_close",
    }
    warn = [] if any(ln.current_price for ln in req.lines) else ["⚠ Няма доставна цена — зарежда се с 0 (влияе на себестойност/оценка)."]
    return await _gate_or_dry(actor, session, action="storeop.load", method="Storeloads_create",
                              body=body, target=f"{len(req.lines)} реда", confirm=req.confirm, warnings=warn)


@router.post("/writeoff")
async def write_off(req: WriteoffReq, actor: User = Depends(current_owner),
                    session: AsyncSession = Depends(get_async_session)):
    """Изписване → Storeouts_save. Envelope CAPTURED from Barsy's own UI (owner clicked «Запиши» with an
    interceptor armed, 2026-07-28), so this is the real shape, not a guess:
    `{id, action_type, values:{…flat header…}, rows:[…]}` — header FLAT in `values`, `rows` a sibling
    (NOT nested elements), ids as strings, `id:0` = new document and `row_id:""` = new row.
    The capture also settled the design question: **reason_id is per ROW**, exactly as this endpoint
    assumed — „Липса" and „Собствена покупка" carry different чл.79/80 ЗДДС treatment, so flattening
    them to one document-level reason would mistag lines. Reasons come from /writeoff-reasons."""
    depot = req.depot_id or get_settings().barsy_depot_id
    names = await _article_names([ln.article_id for ln in req.lines])
    body = {
        "id": 0,                                 # 0 = new document (captured)
        # save_and_close POSTS the document → decrements stock (verified live: 10→9). Plain "save" is
        # a draft (status0) that does NOT move stock; "save_and_close" (status1) is the actual write-off.
        # (The capture itself used "save" — the owner deliberately stopped at the draft step.)
        "action_type": "save_and_close",
        "values": {
            "depot_id": str(depot),
            "type_id": "-1",                     # Глобален (the only write-off type on this install)
            "doc_date": req.doc_date or _now(),
            "description": req.description,
            "deal_id": None,
            "deal_title": "",                    # sent by the UI alongside deal_id
        },
        "rows": [
            # per-row reason (falls back to the document default) — mixing „Липса" and „Собствена
            # покупка" in one document is legitimate and must not be flattened to a single reason
            {"row_id": "",                       # "" = new row (captured)
             "article_id": ln.article_id,        # int here — the capture sends the id unquoted…
             "article_name": ln.article_name or names.get(ln.article_id, ""),  # …and the name beside it
             "amount": _amt(ln.amount),
             "reason_id": str(ln.reason_id or req.reason_id),
             "notes": ln.note or "",
             "lot_value": ""}                    # batch/lot value; empty for non-lot articles
            for ln in req.lines
        ],
    }
    return await _gate_or_dry(actor, session, action="storeop.writeoff", method="Storeouts_save",
                              body=body,
                              target=f"{len(req.lines)} реда / причини "
                                     f"{sorted({ln.reason_id or req.reason_id for ln in req.lines})}",
                              confirm=req.confirm)


@router.post("/transfer")
async def transfer(req: TransferReq, actor: User = Depends(current_owner),
                   session: AsyncSession = Depends(get_async_session)):
    """Прехвърляне → `Storemoves_save`, envelope CAPTURED from Barsy's UI (2026-07-28).

    Uses the captured `Storemoves_save` rather than the documented `Storemoves_create`: the capture is
    what the UI actually sends and is observed to work, the documented variant never was.
    `depot_id_left` = FROM, `depot_id_right` = TO. Row shape = a Storeouts row minus `reason_id`.

    ⚠ `id` is **null** here while `Storeouts_save` wants **0** for a new document. That inconsistency is
    Barsy's, and each is sent verbatim — do NOT factor these two envelopes into one shared builder
    "for consistency"; normalising them would silently break one of the two.

    Requires ≥2 depots — Barsy has only 'Основен' until a 2nd is created, so we hard-check to give a
    clear message rather than a confusing Barsy error."""
    if req.depot_from == req.depot_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Складът-източник и складът-цел трябва да са различни.")
    depots = await _depots()
    if len({d.get("depot_id") for d in depots}) < 2:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Прехвърлянето изисква поне 2 склада в Barsy. В момента съществува само един ('Основен'). "
            "Създайте втори склад в Barsy, за да ползвате тази функция.")
    names = await _article_names([ln.article_id for ln in req.lines])
    body = {
        "id": None,                              # null = new document (captured; NOT 0 as in Storeouts)
        # the capture stopped at "save" (draft). "save_and_close" is what posts the move — same pairing
        # as Storeouts, where save_and_close was verified live to move stock (10→9).
        "action_type": "save_and_close",
        "values": {
            "user_name": None,
            "doc_date": req.doc_date or _now(),
            "description": req.description,
            "deal_id": None, "deal_title": "",
            "depot_id_left": str(req.depot_from),    # FROM
            "depot_id_right": str(req.depot_to),     # TO
        },
        "rows": [
            {"row_id": "", "article_id": ln.article_id,
             "article_name": ln.article_name or names.get(ln.article_id, ""),
             "amount": _amt(ln.amount), "notes": ln.note or "", "lot_value": ""}
            for ln in req.lines
        ],
    }
    return await _gate_or_dry(actor, session, action="storeop.transfer", method="Storemoves_save",
                              body=body, target=f"{req.depot_from}→{req.depot_to} / {len(req.lines)} реда",
                              confirm=req.confirm)


@router.post("/storno")
async def storno(req: StornoReq, actor: User = Depends(current_owner),
                 session: AsyncSession = Depends(get_async_session)):
    """Сторно of a goods-in → book the mirror return (Storeloads_create operation_type 2), restoring
    the prior stock/value. Posted documents are immutable; reversal is the only correction path. The
    caller supplies the lines to reverse (Barsy exposes no Storeloads_get to read them back).
    (Sale storno lives in the COD/orders flow via storno_of_account_id.)"""
    s = get_settings()
    ref = f" #{req.storeload_id}" if req.storeload_id else ""
    body = {
        "store_load": {
            "depot_id": req.depot_id or s.barsy_depot_id,
            "doc_date": _today(),
            "doc_num": _doc_num("STORNO"),
            "operation_type": 2,                 # 2 = return (reverse of a load)
            "currency_id": s.barsy_currency_id,
            "price_mode": s.barsy_price_mode,
            "description": f"Сторно на зареждане{ref}: {req.reason}".strip(),
        },
        "articles": [
            {"article_id": str(ln.article_id), "amount": _amt(ln.amount),
             "current_price": str(ln.current_price if ln.current_price is not None else 0)}
            for ln in req.lines
        ],
        "action_type": "save_and_close",
    }
    return await _gate_or_dry(actor, session, action="storeop.storno", method="Storeloads_create",
                              body=body, target=f"storeload{ref} / {len(req.lines)} реда",
                              confirm=req.confirm)


# ============================================================ Ревизия (stocktake)
class RevisionLine(BaseModel):
    article_id: int
    counted: float = Field(ge=0)          # physically counted quantity (set-to-exact)
    notes: str | None = None
    # Резолюция (reason for the DIFFERENCE) — null on a plain save, REQUIRED by Barsy on close. The
    # asymmetry is the point: count first, justify variances second. See /revision/close.
    rev_res_id: int | None = None


class RevisionReq(BaseModel):
    lines: list[RevisionLine] = Field(min_length=1)
    depot_id: int | None = None
    doc_date: str | None = None
    description: str = ""
    confirm: bool = False


class RevisionCountReq(BaseModel):
    """Fill counted quantities into an ALREADY-OPEN revision (Barsy: Ревизия → Запиши).

    `revision_id` is the id of a revision opened in Barsy — the panel does not open one, because
    Revisions_create's envelope is not captured and a revision's SCOPE (which articles it covers) is
    exactly the thing we must not guess at. The `values` block below must mirror the revision AS OPENED;
    the defaults match the captured document and can be overridden per call."""
    revision_id: int = Field(gt=0)
    lines: list[RevisionLine] = Field(min_length=1)
    amount_date: str                      # effective stock moment of the revision ("YYYY-MM-DD HH:MM:SS")
    depot_id: int | None = None
    cat_id: int | None = None             # scope: category (None = Всички)
    stream_id: int | None = None          # scope: stream  (None = Всички)
    view_mode: str = "1"
    price_mode: str = "1"
    lots_mode: str = "1"
    description: str = ""
    confirm: bool = False


def _num(v: object) -> float | None:
    """Barsy returns amounts as bare numbers, numeric strings, or a {amount: …} wrapper — normalise, and
    return None for anything unreadable so a failed read never masquerades as a real 0."""
    if isinstance(v, dict):
        for k in ("store_amount", "amount", "value"):
            if k in v:
                return _num(v[k])
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


async def _article_stock(article_id: int, depot_id: int | None = None) -> float | None:
    """Current system on-hand for an article, or None if the read FAILED (distinguished from a real 0 —
    presenting a failed read as 0 would show a phantom surplus on the count sheet).

    Primary: `Articles_getStoreAmount {article_id, depot_id}` — UNDOCUMENTED but confirmed live (it is
    what Barsy's own UI calls to fill the Наличност column of a store document). It is depot-scoped,
    which the whole-article read below is not, so it is the correct number for a per-depot count sheet.
    Falls back to the Articles_get read if that method is unavailable on this install."""
    depot = depot_id or get_settings().barsy_depot_id
    try:
        r = await run_in_threadpool(barsy.call, "Articles_getStoreAmount",
                                    {"article_id": article_id, "depot_id": depot})
        n = _num(r)
        if n is not None:
            return n
    except Exception:  # noqa: BLE001 — fall through to the whole-article read
        pass
    try:
        r = await run_in_threadpool(barsy.call, "Articles_get",
                                    {"article_id": article_id, "extra_properties": {"store_amount": 1}})
        a = (r or {}).get("Articles_get") if isinstance(r, dict) else None
        a = a if isinstance(a, dict) else (r if isinstance(r, dict) else {})
        return float(a.get("store_amount") or 0)
    except Exception:  # noqa: BLE001 — a failed read is flagged (None), not silently 0
        return None


@router.post("/revision/preview")
async def revision_preview(req: RevisionReq, _: User = Depends(current_active_user)):
    """Variance per line for a stocktake: counted − system on-hand. READ-ONLY (no write) — the count
    sheet the operator reviews before posting. Negative = липса (shortage), positive = излишък (surplus).
    Stock reads run concurrently (bounded) so a big count doesn't serialise thousands of round-trips;
    a line whose stock read failed is flagged (known=false, system=null) rather than shown as 0."""
    sem = asyncio.Semaphore(8)

    async def _one(aid: int) -> float | None:
        async with sem:
            return await _article_stock(aid, req.depot_id)

    stocks = await asyncio.gather(*[_one(ln.article_id) for ln in req.lines])
    lines = []
    variances: list[float] = []                       # typed → aggregates below stay None-free for mypy
    for ln, sys_amt in zip(req.lines, stocks):
        var = round(ln.counted - sys_amt, 3) if sys_amt is not None else None
        if var is not None:
            variances.append(var)
        lines.append({"article_id": ln.article_id, "system": sys_amt, "counted": ln.counted,
                      "variance": var, "known": sys_amt is not None})
    return {"lines": lines,
            "total_variance": round(sum(variances), 3),
            "shortage_lines": sum(1 for v in variances if v < 0),
            "surplus_lines": sum(1 for v in variances if v > 0),
            "unknown_lines": sum(1 for s in stocks if s is None)}


async def _revision_row(ln: RevisionLine, depot_id: int | None, *, closing: bool = False) -> dict:
    """One Revisions_save row, built from the SAME two reads Barsy's UI makes when a row is picked
    (Articles_get + Articles_getStoreAmount) rather than from invented values. Field names/shape are from
    the captured save.

    `closing` mirrors the two differences the close capture showed: `amount` travels as an INT (a string
    on save) and `rev_res_id` carries the row's resolution instead of null."""
    a, sys_amt = await asyncio.gather(_article(ln.article_id), _article_stock(ln.article_id, depot_id))
    store_amount = sys_amt if sys_amt is not None else 0.0
    diff = round(ln.counted - store_amount, 3)
    price = _num(a.get("actual_price")) or 0
    if closing:
        amount: object = int(ln.counted) if float(ln.counted).is_integer() else ln.counted
        rev_res: object = str(ln.rev_res_id) if ln.rev_res_id is not None else None
    else:
        amount, rev_res = _amt(ln.counted), None
    return {
        "row_unique": ln.article_id,                 # capture: equals article_id for a fresh row
        "article_id": ln.article_id,
        "article_name": a.get("article_name") or "",
        "lot_type_id": a.get("lot_type_id"),
        "ref_num": a.get("ref_num"),
        "amount_unit": _num(a.get("amount_unit")) or 1,
        "amount_type_name_short": a.get("amount_type_name_short") or "",
        "store_amount": store_amount,                # В системата
        "amount": amount,                            # Отчетено (str on save, int on close — captured)
        "diff_1": diff, "diff_2": diff,              # computed difference (UI sends both)
        "actual_price": price, "cur": price,
        "rev_res_id": rev_res, "notes": ln.notes or None, "picture": None,
        "cat_path": a.get("master_cat") or a.get("categories_names") or "",
        # a DOM handle from Barsy's own list widget; echoed back verbatim in the capture. Harmless, but
        # it is UI bookkeeping rather than data — kept only because we send what the UI sends.
        "list_row_id": f"eStructList_1_{ln.article_id}",
    }


async def _revision_body(req: "RevisionCountReq", *, closing: bool) -> tuple[list[dict], dict]:
    """The Revisions_save envelope, shared by the count-save and the close. Identical apart from
    `action_type` and the two per-row differences the close capture showed (see _revision_row)."""
    rows = list(await asyncio.gather(
        *[_revision_row(ln, req.depot_id, closing=closing) for ln in req.lines]))
    return rows, {
        "action_type": "save_and_close" if closing else "save",
        "id": req.revision_id,
        "mode": "changes",                # ONLY the changed rows travel — see revision_count's docstring
        "values": {
            "cat_id": req.cat_id, "view_mode": req.view_mode, "stream_id": req.stream_id,
            "price_mode": req.price_mode, "lots_mode": req.lots_mode,
            "amount_date": req.amount_date,          # the revision's effective stock moment
            "last_revision_amount_date": None,
            "description": req.description,
        },
        "rows": rows,
    }


@router.post("/revision/count")
async def revision_count(req: RevisionCountReq, actor: User = Depends(current_owner),
                         session: AsyncSession = Depends(get_async_session)):
    """Write counted quantities into an OPEN revision → `Revisions_save` with `mode:"changes"`.

    Shape CAPTURED from Barsy's UI (2026-07-28), which also answered the question that blocked the old
    implementation. The owner counted ONE article inside a 38-article revision and pressed «Запиши»; the
    request carried `mode:"changes"` and exactly ONE row. So **a revision save sends only the rows that
    changed, and uncounted articles in scope are neither sent nor zeroed** — the catalogue-wipe risk that
    kept this endpoint disabled does not exist. We therefore send only counted lines, with no defensive
    full-scope padding (padding would be the dangerous move: it would assert counts nobody made).

    The panel does NOT open the revision — `Revisions_create` is uncaptured, and a revision's SCOPE is
    precisely what must not be guessed. Open it in Barsy, then bring its id here. Applying it
    («Приложи промените» — the step that actually corrects stock) is also uncaptured and stays in Barsy;
    see /revision/apply."""
    rows, body = await _revision_body(req, closing=False)
    counted = sum(1 for r in rows if r["diff_1"])
    warn = [] if counted else ["ℹ Нито един ред не се различава от системната наличност — записът няма ефект."]
    return await _gate_or_dry(
        actor, session, action="storeop.revision.count", method="Revisions_save", body=body,
        target=f"ревизия #{req.revision_id} / {len(req.lines)} реда", confirm=req.confirm,
        warnings=warn + ["ℹ Записът само нанася преброените количества. Наличността се коригира чак при "
                         "„Приложи промените“ в Barsy."])


@router.post("/revision/close")
async def revision_close(req: RevisionCountReq, actor: User = Depends(current_owner),
                         session: AsyncSession = Depends(get_async_session)):
    """«Приложи промените» — CLOSE the revision, which is what actually corrects stock.

    Same method as the count-save with `action_type:"save_and_close"` (captured 2026-07-28). Two per-row
    differences the capture showed and this honours: `amount` travels as an INT (a string on save), and
    **`rev_res_id` — the resolution/причина for the difference — is REQUIRED**, where it was null on save.

    That requirement is a real workflow constraint, not a field to auto-fill: count freely, then justify
    each variance before applying it. So a line whose count differs from the system MUST carry its own
    rev_res_id; defaulting one would silently attribute every липса to whatever reason we picked, which is
    precisely the record an inventory audit exists to produce. Lines that match the system need none.

    The close applies the counted quantities to live stock and is irreversible — hence owner-only, the
    gate, and an explicit confirm."""
    rows, body = await _revision_body(req, closing=True)
    missing = [r["article_id"] for r in rows if r["diff_1"] and not r["rev_res_id"]]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Липсва резолюция (причина за разликата) за артикул(и): {', '.join(map(str, missing))}. "
            f"Barsy изисква причина за всеки ред с разлика при приключване на ревизия.")
    differing = sum(1 for r in rows if r["diff_1"])
    return await _gate_or_dry(
        actor, session, action="storeop.revision.close", method="Revisions_save", body=body,
        target=f"ревизия #{req.revision_id} / {len(rows)} реда / {differing} с разлика",
        confirm=req.confirm,
        warnings=["🚨 ПРИКЛЮЧВАНЕТО КОРИГИРА НАЛИЧНОСТИТЕ и не може да се върне назад."])
