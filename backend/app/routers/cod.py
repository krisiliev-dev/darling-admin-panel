# -*- coding: utf-8 -*-
"""COD reconciliation — наложен платеж. Bulgarian couriers collect cash at the door and pay
the merchant out twice-weekly against a deduction file; COD is 55–60% of orders, so tracking
what's collected-but-not-yet-paid (and catching discrepancies) is a core daily job.

Self-contained off fulfillment_state (the COD amount is captured at label time) + a panel-owned
cod_settlement table. Empty until the store is live, but the reconciliation logic is real."""
import csv
import io
import re
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import connector, shopify_orders
from ..core import courier as courier_mod
from ..db import get_async_session
from ..models import CodSettlement, User
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/cod", tags=["cod"])

# fulfillment status → reconciliation bucket
_BUCKET = {
    "new": "preship", "picking": "preship", "packed": "preship",
    "shipped": "in_transit",     # COD out with the courier, not collected yet
    "delivered": "awaiting",     # courier collected the cash, owes us the payout
    "cod_paid": "settled",       # reconciled
    "returned": "rto",           # returned — no COD
}
BUCKET_BG = {"preship": "подготовка", "in_transit": "в обращение",
             "awaiting": "за изплащане", "settled": "разчетено", "rto": "върната"}


def _cod_orders():
    """COD orders (cod_amount>0) from fulfillment_state."""
    smap = shopify_orders.state_map()
    return {oid: s for oid, s in smap.items() if (s.get("cod_amount") or 0) > 0}


# --- courier payout-file parsing (Speedy/Econt deduction sheets) ---------------------
# Couriers export payout files in inconsistent shapes (comma OR semicolon, Cyrillic headers,
# comma-decimal amounts, cp1251 or utf-8). We sniff rather than hard-code one carrier's format.
_TRACK_HINTS = ("track", "awb", "товарителниц", "номер", "barcode", "баркод", "пратк", "shipment")
_AMOUNT_HINTS = ("нал", "cod", "наложен", "сума", "amount", "плат", "collected", "получен")


def _norm_tracking(v) -> str:
    return re.sub(r"\D", "", str(v or ""))  # couriers pad/space tracking numbers; compare digits only


def _parse_amount(v):
    s = re.sub(r"[^\d,.\-]", "", str(v or "")).strip(".,")  # drop currency/text + stray edge separators
    if not s:
        return None
    # the RIGHTMOST separator is the decimal point; any others are thousands separators. Handles
    # "1.234,56", "1,234.56", "19,99", "25.50" alike.
    sep = max(s.rfind(","), s.rfind("."))
    num = (re.sub(r"[.,]", "", s[:sep]) + "." + s[sep + 1:]) if sep != -1 else s
    try:
        return round(float(num), 2)
    except ValueError:
        return None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251", "latin-1"):  # BG couriers commonly ship cp1251
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_payout(text: str) -> dict:
    """→ {delimiter, columns:{tracking,amount}, rows:[{tracking,amount}], skipped}. Tolerant: sniffs
    the delimiter, finds tracking/amount columns by header keyword, falls back to positional."""
    sample = "\n".join(text.splitlines()[:20])
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = max(",;\t|", key=sample.count)  # sniff can fail on ragged files
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return {"delimiter": delim, "columns": {}, "rows": [], "skipped": 0}

    header = [c.strip().lower() for c in rows[0]]
    has_header = any(any(h in cell for h in _TRACK_HINTS) for cell in header)
    body = rows[1:] if has_header else rows

    def _cands(hints):
        return [i for i, cell in enumerate(header) if any(h in cell for h in hints)]

    def _col_has_numbers(col):
        return col is not None and any(
            _parse_amount(r[col]) is not None for r in body if col < len(r))

    def _track_score(col):
        # a waybill is a long digit string; a client id is short — pick the longest-digit column
        lens = [len(_norm_tracking(r[col])) for r in body if col < len(r) and _norm_tracking(r[col])]
        return sum(lens) / len(lens) if lens else 0

    def _money_score(col):
        # a money column has decimal values ("25.50"); a payer/client id is a bare integer
        vals = [v for v in (_parse_amount(r[col]) for r in body if col < len(r)) if v is not None]
        return sum(1 for v in vals if abs(v - round(v)) > 0.001) / len(vals) if vals else -1.0

    # DECOY-AWARE column choice: a header keyword can match the wrong column ("Номер на клиент" hits
    # the 'номер' track hint; a numeric "Платец" hits 'плат' + parses as a number). So among the
    # header-matched candidates, pick by VALUE shape — the longest-digit column for tracking, and the
    # most money-like (decimals), rightmost-on-tie, for amount.
    if has_header:
        t_c = _cands(_TRACK_HINTS)
        t_col = max(t_c, key=_track_score) if t_c else 0
        a_c = [c for c in _cands(_AMOUNT_HINTS) if c != t_col and _col_has_numbers(c)]
        a_col = max(a_c, key=lambda c: (_money_score(c), c)) if a_c else None
    else:
        t_col, a_col = 0, None
    if a_col is None:  # positional fallback: the rightmost column that parses as a number
        for i in range(len(rows[0]) - 1, -1, -1):
            if i != t_col and _col_has_numbers(i):
                a_col = i
                break

    out, skipped = [], 0
    for r in body:
        tracking = _norm_tracking(r[t_col]) if t_col < len(r) else ""
        amount = _parse_amount(r[a_col]) if (a_col is not None and a_col < len(r)) else None
        if tracking and amount is not None:
            out.append({"tracking": tracking, "amount": amount})
        else:
            skipped += 1
    cols = {"tracking": header[t_col] if has_header and t_col < len(header) else f"col{t_col}",
            "amount": header[a_col] if has_header and a_col is not None and a_col < len(header)
            else (f"col{a_col}" if a_col is not None else None)}
    return {"delimiter": delim, "columns": cols, "rows": out, "skipped": skipped}


@router.get("")
async def cod_overview(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    orders = await run_in_threadpool(_cod_orders)  # sync sqlite off the event loop
    settlements = {s.order_id: s for s in (await session.execute(
        select(CodSettlement))).scalars().all()}

    out = []
    summ: dict[str, Any] = {"in_transit_amount": 0.0, "awaiting_amount": 0.0, "settled_amount": 0.0,
            "in_transit_count": 0, "awaiting_count": 0, "settled_count": 0,
            "discrepancy_count": 0, "discrepancy_amount": 0.0}
    for oid, s in orders.items():
        status_key = s.get("status") or "new"
        bucket = _BUCKET.get(status_key, "preship")
        expected = round(float(s.get("cod_amount") or 0), 2)
        st = settlements.get(oid)
        collected = round(st.collected, 2) if st else None
        discrepancy = (st is not None and abs(st.collected - st.expected) > 0.01)
        row = {
            "order_id": oid, "order_name": s.get("order_name") or oid,
            "courier": s.get("courier") or "", "tracking": s.get("tracking") or "",
            "status": status_key, "bucket": bucket, "bucket_bg": BUCKET_BG[bucket],
            "expected": expected, "collected": collected, "discrepancy": discrepancy,
            # last state change ≈ when the courier collected (→ awaiting); the frontend ages from it
            "updated_ts": s.get("updated_ts"),
        }
        out.append(row)
        if bucket == "in_transit":
            summ["in_transit_amount"] += expected; summ["in_transit_count"] += 1
        elif bucket == "awaiting":
            summ["awaiting_amount"] += expected; summ["awaiting_count"] += 1
        elif bucket == "settled":
            # a genuine 0.00 collection must count as 0, not fall back to expected
            summ["settled_amount"] += (collected if collected is not None else expected)
            summ["settled_count"] += 1
        if discrepancy and st is not None:
            summ["discrepancy_count"] += 1
            summ["discrepancy_amount"] += abs(st.collected - st.expected)  # €, not just a count
    for k in ("in_transit_amount", "awaiting_amount", "settled_amount", "discrepancy_amount"):
        summ[k] = round(summ[k], 2)
    # outstanding = money in the pipeline the courier hasn't paid us yet
    summ["outstanding_amount"] = round(summ["in_transit_amount"] + summ["awaiting_amount"], 2)
    # most recent courier payout date on record — anchors the "since last payout" cue
    summ["last_payout"] = max((s.payout_date for s in settlements.values() if s.payout_date), default="")
    out.sort(key=lambda r: (r["bucket"] != "awaiting", r["order_name"]))
    return {"summary": summ, "orders": out}


@router.get("/barsy-booked")
async def barsy_booked_cod(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """The AUTHORITATIVE 'expected COD' — the sales Barsy has booked PAID under method 6 (via the
    connector's list_cod_accounts). Under the anonymous-paid model every finalised order is a method-6
    COD sale, so this is 'what Barsy believes it collected'. Cross-referenced against the panel's
    settlement state so staff can spot Barsy-booked COD that NO courier has remitted → uncollected /
    storno candidates. Skips already-stornoed accounts. Read-only."""
    try:
        accounts = await run_in_threadpool(connector.list_cod_accounts)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Справката не е налична (connector.list_cod_accounts липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy недостъпен: {str(e)[:160]}")
    settled = {oid for (oid,) in (await session.execute(select(CodSettlement.order_id))).all()}
    smap = await run_in_threadpool(shopify_orders.state_map)
    rows, total, uncollected = [], 0.0, 0
    for a in (accounts or []):
        if not isinstance(a, dict) or a.get("stornoed"):
            continue                                    # already reversed — not owed to us
        oid = str(a.get("order_id") or "")
        amt = round(float(a.get("amount") or 0), 2)
        total += amt
        st = smap.get(oid) or {}
        bucket = _BUCKET.get(str(st.get("status") or ""), "preship")
        is_settled = oid in settled or bucket == "settled"
        # booked paid in Barsy but neither collected/settled nor returned → the storno-candidate signal
        is_uncollected = not is_settled and bucket != "rto"
        if is_uncollected:
            uncollected += 1
        rows.append({
            "order_id": oid, "order_ref": a.get("order_ref") or oid, "amount": amt,
            "tracking_number": a.get("tracking_number"), "finalised_ts": a.get("finalised_ts"),
            "settled": is_settled, "bucket": bucket, "uncollected": is_uncollected,
        })
    rows.sort(key=lambda r: (not r["uncollected"], r["order_ref"]))
    return {"count": len(rows), "total": round(total, 2), "uncollected": uncollected, "accounts": rows}


@router.post("/sync-tracking")
async def sync_tracking(
    actor: User = Depends(require_roles("staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    """Poll couriers for shipped COD orders and advance delivered ones. Real courier calls."""
    updated = 0
    orders = await run_in_threadpool(_cod_orders)
    for oid, s in orders.items():
        if _BUCKET.get(s.get("status")) != "in_transit":
            continue
        key, tracking = s.get("courier"), s.get("tracking")
        if not key or not tracking:
            continue
        try:
            tr = await run_in_threadpool(courier_mod.get(key).track, tracking)
        except Exception:
            continue  # a single courier hiccup shouldn't abort the sweep
        if tr.get("delivered"):
            await run_in_threadpool(shopify_orders.set_state, oid, s.get("order_name") or oid,
                                    status="delivered")
            updated += 1
    await log_action(session, actor, "cod.sync_tracking", detail=f"delivered+={updated}")
    return {"ok": True, "delivered": updated}


class SettleLine(BaseModel):
    order_id: str = Field(max_length=64)
    collected: float = Field(ge=0, le=1_000_000)  # cash collected; never negative


class SettleRequest(BaseModel):
    courier: str = Field("", max_length=64)
    payout_date: str = Field("", max_length=32)
    reference: str = Field("", max_length=128)
    # cap the batch — one payout sheet is dozens of orders, not tens of thousands; without a
    # bound a single request could fan out to that many per-line commits / Barsy calls.
    lines: list[SettleLine] = Field(max_length=1000)


@router.post("/settle")
async def settle(
    req: SettleRequest,
    actor: User = Depends(require_roles("staff")),  # financial reconciliation — staff/owner only
    session: AsyncSession = Depends(get_async_session),
):
    """Record a courier payout: mark orders cod_paid, store settlement rows, flag discrepancies.
    Idempotent: only settles orders still awaiting/in-transit, skips any already-settled one, and
    each order gets AT MOST one settlement row (unique order_id + per-line commit)."""
    if not req.lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове за разчет.")
    orders = await run_in_threadpool(_cod_orders)
    already = {oid for (oid,) in (await session.execute(select(CodSettlement.order_id))).all()}
    settled = discrepancies = skipped = 0
    for ln in req.lines:
        s = orders.get(ln.order_id)
        # gate: exists, still owed to us (awaiting/in-transit), not already settled
        if not s or _BUCKET.get(s.get("status")) not in ("awaiting", "in_transit") \
                or ln.order_id in already:
            skipped += 1
            continue
        expected = round(float(s.get("cod_amount") or 0), 2)
        collected = round(float(ln.collected), 2)
        try:
            # Persist the FINANCIAL record first and commit it. set_state writes a DIFFERENT db
            # (pipeline sqlite, outside this transaction), so if we flipped status first and the
            # commit then failed, the order would read "paid" with no settlement row — an
            # unrecoverable orphan. A settlement row without the status flip is self-healing.
            session.add(CodSettlement(
                order_id=ln.order_id, order_name=s.get("order_name") or ln.order_id,
                # the order's OWN courier is authoritative; a batch-level courier can be wrong for
                # mixed carriers, so it's only a fallback when the order has none recorded.
                courier=s.get("courier") or req.courier or "",
                waybill=s.get("tracking") or "",  # join key for future courier-payout-API auto-match
                expected=expected,
                collected=collected, payout_ref=req.reference, payout_date=req.payout_date,
                settled_by=actor.email))
            await session.commit()
        except Exception:
            await session.rollback()
            skipped += 1
            continue
        # settlement is durable; now advance the fulfillment status (re-derivable if it fails)
        try:
            await run_in_threadpool(shopify_orders.set_state, ln.order_id,
                                    s.get("order_name") or ln.order_id, status="cod_paid")
        except Exception:
            pass  # status flip is a projection of the settlement row; a nightly sync can repair it
        already.add(ln.order_id)
        settled += 1
        # ⚠ do NOT post a payment to Barsy here. Under the anonymous-PAID model the sale is booked PAID
        # IN FULL at packing (finalise → method 6, total_remain 0), so posting the collection again would
        # DOUBLE-PAY the account (total_paid > total_sum) and double-count the COD cash. Settle now = just
        # CONFIRM the courier remittance in the panel (the settlement row + status flip). A matched COD
        # needs no Barsy write; only the UNCOLLECTED path writes to Barsy (storno). See cod_storno.
        if abs(collected - expected) > 0.01:
            discrepancies += 1
    await log_action(session, actor, "cod.settle", target=req.reference,
                     detail=f"settled={settled} discrepancies={discrepancies} skipped={skipped}")
    return {"ok": True, "settled": settled, "discrepancies": discrepancies, "skipped": skipped}


class StornoRequest(BaseModel):
    reason: str = Field("COD не е потърсена / върната пратка", max_length=200)
    confirm: bool = False


@router.post("/{order_id}/storno")
async def cod_storno(
    order_id: str, req: StornoRequest,
    actor: User = Depends(require_roles("staff")),   # financial reversal — staff/owner only
    session: AsyncSession = Depends(get_async_session),
):
    """Uncollected/returned COD → STORNO the Barsy sale (reverse the paid-at-packing booking + return the
    goods to stock). Under the anonymous-PAID model a COD sale is booked PAID when the label prints, so an
    order the courier never collected (RTO) sits FALSELY 'paid' in Barsy — this is the accuracy backstop
    that catches it. Gated (COD_BARSY_POST_LIVE) + confirm + role + audit. Flips the order to 'returned'
    so it drops out of the awaiting/reconcile buckets."""
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Сторното е реален запис в Barsy — потвърди с confirm=true.")
    orders = await run_in_threadpool(_cod_orders)
    s = orders.get(order_id)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "COD поръчката не е намерена.")
    if _BUCKET.get(s.get("status")) == "settled":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{s.get('order_name') or order_id} е разчетена (събрана) — не сторнирай платена поръчка.")
    live = get_settings().cod_barsy_post_live
    try:
        result = await run_in_threadpool(connector.cod_refused_to_barsy, str(order_id), req.reason, live)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Сторното не е налично (connector.cod_refused_to_barsy липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    # projection: flip to 'returned' so it leaves the awaiting bucket (self-heals from a nightly sync)
    try:
        await run_in_threadpool(shopify_orders.set_state, order_id,
                                s.get("order_name") or order_id, status="returned")
    except Exception:
        pass
    await log_action(session, actor, "cod.storno", target=s.get("order_name") or order_id,
                     detail=str(result)[:300])
    return {"ok": True, "order_id": order_id, "live": live, "result": result}


# NB: the old `_post_cod_payment_to_barsy` (settle → connector.cod_payment_to_barsy, paymethod 5) is
# GONE — under the anonymous-PAID model the sale is already paid in full at packing, so re-posting the
# collection would double-pay. Matched COD settles panel-side only; see the note in `settle`.


_MAX_PAYOUT_BYTES = 5 * 1024 * 1024  # a payout sheet is KBs; cap to avoid a memory-blowout upload


@router.post("/import-payout")
async def import_payout(
    file: UploadFile = File(...),
    actor: User = Depends(require_roles("staff")),  # same gate as settle — financial reconciliation
    session: AsyncSession = Depends(get_async_session),
):
    """Parse a courier payout/deduction file (CSV) and match its rows to our awaiting COD orders by
    tracking number. Returns a PREVIEW only — writes nothing. The operator reviews it and submits the
    pre-filled lines through /settle (kept as the single write path). Surfaces three lists:
      • matched            — file row paired to an awaiting order (with discrepancy flag)
      • unmatched_rows     — payout lines with no order on our side (paid-not-delivered / unknown)
      • delivered_not_paid — awaiting orders absent from the file (courier owes us / missing)
    """
    raw = await file.read(_MAX_PAYOUT_BYTES + 1)   # bounded read — don't buffer an oversized upload
    if len(raw) > _MAX_PAYOUT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файлът е твърде голям.")
    parsed = _parse_payout(_decode(raw))
    if not parsed["rows"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Неразпознат файл — няма редове с товарителница и сума.")

    orders = await run_in_threadpool(_cod_orders)
    result = _reconcile(parsed["rows"], orders)
    await log_action(session, actor, "cod.import_payout", target=file.filename or "",
                     detail=f"matched={len(result['matched'])} unmatched={len(result['unmatched_rows'])} "
                            f"missing={len(result['delivered_not_paid'])}")
    return {"file": file.filename, "delimiter": parsed["delimiter"], "columns": parsed["columns"],
            "skipped_rows": parsed["skipped"], **result}


def _reconcile(rows: list[dict], orders: dict) -> dict:
    """Match payout rows (each {tracking, amount, [fee, payout_date]}) to our awaiting COD orders by
    tracking number → the buckets the Разчети UI renders. Shared by the file importer and the
    courier auto-pull. Writes nothing — a preview + ready-to-submit settle_lines."""
    by_tracking, awaiting_oids = {}, set()
    for oid, s in orders.items():
        bucket = _BUCKET.get(s.get("status"), "preship")
        if bucket in ("awaiting", "in_transit"):
            awaiting_oids.add(oid)
            tr = _norm_tracking(s.get("tracking"))
            if tr:
                by_tracking[tr] = (oid, s, bucket)

    matched, unmatched, matched_oids = [], [], set()
    for r in rows:
        hit = by_tracking.get(r["tracking"])
        if not hit:
            unmatched.append({"tracking": r["tracking"], "collected": r["amount"]})
            continue
        oid, s, bucket = hit
        expected = round(float(s.get("cod_amount") or 0), 2)
        out_of_range = not (0 <= r["amount"] <= 1_000_000)
        matched.append({
            "order_id": oid, "order_name": s.get("order_name") or oid,
            "tracking": r["tracking"], "courier": s.get("courier") or "",
            "expected": expected, "collected": r["amount"], "bucket": bucket,
            "fee": r.get("fee"), "payout_date": r.get("payout_date"),
            "discrepancy": abs(r["amount"] - expected) > 0.01, "out_of_range": out_of_range,
        })
        matched_oids.add(oid)

    delivered_not_paid = [
        {"order_id": oid, "order_name": orders[oid].get("order_name") or oid,
         "expected": round(float(orders[oid].get("cod_amount") or 0), 2),
         "tracking": _norm_tracking(orders[oid].get("tracking"))}
        for oid in awaiting_oids
        if oid not in matched_oids and _BUCKET.get(orders[oid].get("status")) == "awaiting"
    ]
    return {
        "matched": matched, "unmatched_rows": unmatched, "delivered_not_paid": delivered_not_paid,
        "settle_lines": [{"order_id": m["order_id"], "collected": m["collected"]}
                         for m in matched if not m["out_of_range"]],
    }


class ReconcileAutoRequest(BaseModel):
    courier: str                     # speedy | econt
    date_from: str = Field(..., max_length=10)   # YYYY-MM-DD
    date_to: str = Field(..., max_length=10)


@router.post("/reconcile-auto")
async def reconcile_auto(req: ReconcileAutoRequest,
                         actor: User = Depends(require_roles("staff")),
                         session: AsyncSession = Depends(get_async_session)):
    """Auto-pull a courier's COD payout report for a window and reconcile it against our orders by
    waybill — same preview shape as the manual file import, so the Разчети UI renders it unchanged.
    (Speedy /payments, Econt CD_send_sum — validated live at launch once shipments exist.)"""
    name = req.courier.strip().lower()
    if name not in ("speedy", "econt"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Куриерът трябва да е speedy или econt.")
    adapter = courier_mod.get(name)
    if not adapter.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} не е конфигуриран.")
    try:
        rows = await run_in_threadpool(adapter.payout_report, req.date_from, req.date_to)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{name}: {str(e)[:160]}")
    norm = []
    for r in (rows or []):
        tr = _norm_tracking(r.get("tracking") or r.get("waybill"))
        if not tr:
            continue
        # presence-based, not truthiness — a legitimately collected 0.00 must not fall through to
        # `amount`; _parse_amount tolerates comma-decimals ("19,99") from the courier feed.
        raw_amt = r["collected"] if "collected" in r else r.get("amount")
        norm.append({"tracking": tr, "amount": _parse_amount(raw_amt) or 0.0,
                     "fee": r.get("fee"), "payout_date": r.get("payout_date")})
    orders = await run_in_threadpool(_cod_orders)
    result = _reconcile(norm, orders)
    await log_action(session, actor, "cod.reconcile_auto", target=name,
                     detail=f"from={req.date_from} to={req.date_to} pulled={len(norm)} matched={len(result['matched'])}")
    return {"courier": name, "date_from": req.date_from, "date_to": req.date_to,
            "rows_pulled": len(norm), **result}
