# -*- coding: utf-8 -*-
"""Доставки — receiving deliveries (goods-in). The Доставки page's primary flow.

Journey: pick supplier + warehouse → upload the supplier's Excel/CSV (or a photo → OCR) → the sheet
is parsed and each row matched against our catalogue (existing vs NEW). Staff scan-confirm quantities
with a handheld barcode scanner (line goes green when scanned == claimed, red on a mismatch). NEW
products flow to Приемане to be enriched + created in Barsy. Only when EVERY line has a Barsy article
does FINALISE fire — sending the whole delivery as ONE Barsy storeload.

State is panel-owned staging (DeliveryBatch/DeliveryLine in admin.db); Barsy stays system of record.
The storeload write reuses the verified helpers + the DELIVERY_BARSY_LIVE gate from intake_delivery.
"""
import csv
import datetime as _dt
import io
import math
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import Integer, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..barsy_ids import extract_id
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import (
    DeliveryBatch,
    DeliveryLine,
    IntakeDraft,
    SupplierClaim,
    SupplierImportProfile,
    User,
    Zayavka,
    ZayavkaLine,
)
from ..services import delivery_parse as DP
from ..services.text import norm
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])
# static routes (import-profiles, preview) live on their own router, included BEFORE the
# main one, so GET /api/deliveries/import-profiles is not shadowed by GET /{batch_id}
profiles_router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])


# ------------------------------------------------------------------ parsing (xlsx + csv) ---
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024   # cap uploads (sheet + photo) — guards memory / zip-bombs


def _check_size(raw: bytes) -> None:
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файлът е празен.")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Файлът е твърде голям (макс. {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")


def _rows_from_upload(filename: str, raw: bytes) -> list[list]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return DP.read_rows(raw)
    if name.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        # sniff delimiter (BG exports use ; or ,)
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
            delim = dialect.delimiter
        except csv.Error:
            delim = ";" if text[:2048].count(";") >= text[:2048].count(",") else ","
        return [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Качи Excel (.xlsx) или CSV файл.")


def _match_row(supplier: str, code: str, name: str, qty: float, price: float, barcode: str = "") -> dict:
    """Match one raw row against the catalogue → a normalised line dict (existing vs new).
    Shared by the sheet parser, the edited-rows path and OCR."""
    m = DP.match_existing(supplier, code, name)
    if price <= 0 and m and m.get("delivery_price"):
        price = DP.to_float(m["delivery_price"])
    if not barcode and m:
        # a known EAN for an existing product lets the scanner confirm it even if the doc had none
        eans = DP.barcode_index()["code_to_eans"].get(m["code"]) or []
        barcode = eans[0] if eans else ""
    return {
        "supplier_code": code, "name": (m["name"] if m else name), "qty": qty, "price": price,
        "barcode": barcode, "is_new": m is None,
        "match_code": (m or {}).get("code", ""), "match_method": (m or {}).get("method", ""),
        "match_confidence": (m or {}).get("confidence", ""),
    }


def _parse_rows(rows: list[list], supplier: str,
                cols: dict | None = None, data_row: int | None = None) -> list[dict]:
    """Detect columns (or use an explicit per-supplier mapping) and match each data row → a
    normalised line dict. With `cols` given, `data_row` is the 1-based first PRODUCT row in the
    ORIGINAL sheet — exact coordinates, so a blank row 1 + header on row 2 is just data_row 3.
    Without a mapping we strip leading blank rows first, which is the header-row bug's fix on the
    auto-detect path."""
    rows = rows or []
    if cols is not None:
        start = max(0, (data_row or 1) - 1)
        data_rows = rows[start:]
        use = cols
    else:
        stripped = DP.strip_leading_blank(rows)
        if not stripped:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове в таблицата.")
        detected = DP.detect_columns(stripped[0])
        if not detected:
            use = {"code": 0, "name": 1, "qty": 2, "price": 3}
            data_rows = stripped
        else:
            use = detected
            data_rows = stripped[1:]
    if not data_rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове за приемане.")
    out = []
    for r in data_rows:
        def cell(field, r=r):        # bind r per-iteration (closure is used within this iteration)
            j = use.get(field)
            if j is None or j < 0:
                return None
            return r[j] if j < len(r) else None
        nm = str(cell("name") or "").strip()
        code = str(cell("code") or "").strip()
        if not nm and not code:
            continue
        out.append(_match_row(supplier, code, nm, DP.to_float(cell("qty")),
                              DP.to_float(cell("price")), str(cell("barcode") or "").strip()))
    return out


# ------------------------------------------------------------------ serialisation ---
def _line_out(ln: DeliveryLine) -> dict:
    return {
        "id": ln.id, "supplier_code": ln.supplier_code, "name": ln.name, "qty": ln.qty,
        "price": ln.price, "barcode": ln.barcode, "is_new": ln.is_new, "match_code": ln.match_code,
        "match_method": ln.match_method, "match_confidence": ln.match_confidence,
        "article_id": ln.article_id, "scanned_qty": ln.scanned_qty, "status": ln.status,
        "defect_qty": ln.defect_qty, "received": (ln.qty or 0) - (ln.defect_qty or 0),
        "intake_draft_id": ln.intake_draft_id,
    }


def _batch_summary(b: DeliveryBatch, lines: list[DeliveryLine]) -> dict:
    new_pending = sum(1 for ln in lines if ln.is_new and ln.article_id is None)
    return {
        "id": b.id, "supplier": b.supplier, "supplier_id": b.supplier_id, "depot_id": b.depot_id,
        "reference": b.reference, "doc_date": b.doc_date, "source": b.source, "status": b.status,
        "storeload_id": b.storeload_id, "doc_num": b.doc_num,
        "finalise_started_at": b.finalise_started_at.isoformat() if b.finalise_started_at else None,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "finalised_at": b.finalised_at.isoformat() if b.finalised_at else None,
        "total": len(lines), "new": sum(1 for ln in lines if ln.is_new),
        "existing": sum(1 for ln in lines if not ln.is_new),
        "confirmed": sum(1 for ln in lines if ln.status == "confirmed"),
        "mismatched": sum(1 for ln in lines if ln.status == "mismatch"),
        "new_pending": new_pending,
    }


async def _lines(session: AsyncSession, batch_id: int) -> list[DeliveryLine]:
    return list((await session.execute(
        select(DeliveryLine).where(DeliveryLine.batch_id == batch_id)
        .order_by(DeliveryLine.is_new, DeliveryLine.id))).scalars().all())  # existing first


async def _detail(session: AsyncSession, b: DeliveryBatch) -> dict:
    lines = await _lines(session, b.id)
    return {**_batch_summary(b, lines), "lines": [_line_out(ln) for ln in lines]}


def _recompute_status(b: DeliveryBatch, lines: list[DeliveryLine]) -> None:
    """awaiting_products while any NEW product is still un-added (no Barsy article yet); otherwise
    ready. Existing lines resolve their article_id at finalise, so they don't hold the status back."""
    if b.status == "finalised":
        return
    b.status = "awaiting_products" if any(ln.is_new and ln.article_id is None for ln in lines) else "ready"


def _catalog_ids_for_codes(codes: set) -> dict:
    """{code → Barsy article_id} for JUST the codes we need, resolved with per-code filters.
    Avoids pulling the whole catalogue (`length: 100000`) — that single huge call is the one most
    likely to hit the low-tier Barsy read-timeout + its 4× retry, hanging finalise."""
    out: dict = {}
    for c in {str(x) for x in codes if x}:
        try:
            rows = barsy.call("Articles_getlist", {"filters": {"code": c}, "length": 5}) or []
        except Exception:
            continue
        for a in rows:
            if str(a.get("code")) == c and a.get("article_id"):
                out[c] = a.get("article_id")
                break
    return out


async def autofinalise_if_ready(session: AsyncSession, batch_id: int | None, actor: User) -> dict:
    """Called after a NEW product is created in Barsy (from Нов продукт save): if the delivery now has
    ALL its new products created and every existing line resolved, send its storeload automatically —
    ONE Barsy delivery, no manual step (owner asked for this). Not ready → {ready: False}, no write.

    Only reached on the live path (caller already checked DELIVERY_BARSY_LIVE). The scan-confirm gate
    is NOT enforced here — the owner defined the trigger as 'all products created'; the manual Доставки
    finalise keeps the scan gate for the careful path."""
    if batch_id is None:
        return {"ready": False}
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        return {"ready": False}
    if b.status == "finalised":
        return {"ready": True, "sent": False, "already": True, "storeload_id": b.storeload_id}
    lines = await _lines(session, batch_id)
    if not lines or any(ln.qty <= 0 for ln in lines):
        return {"ready": False}
    if any(ln.is_new and ln.article_id is None for ln in lines):
        return {"ready": False}                      # still new products to create
    # The owner's trigger is 'all products created', NOT a scan gate — but if someone is
    # part-way through counting (some lines scanned, not all confirmed), booking now would
    # freeze the delivery at document quantities and make their shortage unrecordable.
    # Yield and let them finish; the manual finalise is still there.
    if any(ln.scanned_qty for ln in lines) and any(ln.status != "confirmed" for ln in lines):
        return {"ready": False, "scan_in_progress": True}
    # resolve any existing lines' Barsy id by code
    to_resolve = [ln for ln in lines if ln.article_id is None]
    if to_resolve:
        try:
            cmap = await run_in_threadpool(_catalog_ids_for_codes, {ln.match_code for ln in to_resolve})
        except Exception:
            return {"ready": False}
        for ln in to_resolve:
            aid = cmap.get(ln.match_code)
            if aid is not None:
                ln.article_id = aid
    if any(ln.article_id is None for ln in lines):
        return {"ready": False}                      # something still unresolved → wait
    # atomic claim so two concurrent saves can't double-send the storeload
    claim = await session.execute(
        update(DeliveryBatch).where(DeliveryBatch.id == batch_id,
                                    DeliveryBatch.status.not_in(("finalised", "finalising")))
        .values(status="finalising"))
    await session.commit()
    if claim.rowcount == 0:  # type: ignore[attr-defined]  # SQLAlchemy/fastapi-users stub gap, not a bug
        return {"ready": True, "sent": False, "already": True}
    doc_num = ((b.reference or "DZ").strip().replace(" ", "-")[:36] or "DZ") + f"-B{b.id}"
    b.doc_num = doc_num                      # persisted so /reconcile can probe Barsy for it
    b.finalise_started_at = _dt.datetime.now(_dt.UTC)
    await session.commit()
    _cfg = get_settings()
    payload = {
        "store_load": {
            "depot_id": b.depot_id, "doc_date": b.doc_date or time.strftime("%Y-%m-%d"),
            "doc_num": doc_num, "operation_type": 1, "doc_type_id": 1,
            # prices are EUR + VAT-inclusive — see barsy_currency_id / barsy_price_mode (COGS correctness)
            "currency_id": _cfg.barsy_currency_id, "price_mode": _cfg.barsy_price_mode,
        },
        # received = claimed - defective; a fully-defective line is not booked at all
        "articles": [{"article_id": int(ln.article_id),  # type: ignore[arg-type]  # SQLAlchemy/fastapi-users stub gap, not a bug
                      "amount": str((ln.qty or 0) - (ln.defect_qty or 0)),
                      "current_price": str(ln.price)}
                     for ln in lines if (ln.qty or 0) - (ln.defect_qty or 0) > 0],
        "action_type": "save_and_close",
    }
    if b.supplier_id:
        payload["store_load"]["supplier_id"] = b.supplier_id  # type: ignore[index]  # SQLAlchemy/fastapi-users stub gap, not a bug
    try:
        out = await run_in_threadpool(barsy.call, "Storeloads_create", payload)
    except barsy.BarsyUncertainError as e:
        # Barsy may already hold this storeload. Releasing the claim would invite a retry that
        # books it twice, so stay 'finalising' and wait for /reconcile.
        await session.commit()
        return {"ready": True, "sent": False, "uncertain": True, "error": str(e)[:160]}
    except Exception as e:
        b.status = "ready"                           # release the claim so it can be retried
        await session.commit()
        return {"ready": True, "sent": False, "error": str(e)[:160]}
    b.storeload_id = extract_id(out, ("storeload_id", "id"))   # a storeload, never an article
    b.status = "finalised"
    b.finalised_at = _dt.datetime.now(_dt.UTC)
    await _flip_zayavka_received(session, b)
    await session.commit()
    await log_action(session, actor, "delivery.autofinalise", target=b.supplier or b.reference,
                     detail=f"batch={b.id} storeload={b.storeload_id} lines={len(lines)}")
    return {"ready": True, "sent": True, "storeload_id": b.storeload_id}


# ------------------------------------------------------------------ create (upload) ---
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_delivery(
    file: UploadFile = File(...),
    supplier: str = Form(""),
    supplier_id: int = Form(0),
    depot_id: int = Form(1),
    reference: str = Form(""),
    doc_date: str = Form(""),
    # explicit per-supplier column mapping (0-based; -1 = absent). Sent when the user maps in
    # the wizard; col_name < 0 means "no mapping supplied, auto-detect / use saved profile".
    col_code: int = Form(-2),
    col_name: int = Form(-2),
    col_qty: int = Form(-2),
    col_price: int = Form(-2),
    col_barcode: int = Form(-2),
    data_row: int = Form(0),
    save_profile: bool = Form(False),
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Upload a supplier delivery sheet → parse + match → persist a draft batch with its lines.
    NEW lines each get a linked intake draft so they surface on the Приемане page for enrichment.

    Column resolution, in order: an explicit mapping from the wizard; else this supplier's saved
    profile; else auto-detect. Saving a profile remembers the mapping for next time."""
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)   # bounded read — don't buffer an oversized upload
    _check_size(raw)
    try:
        rows = await run_in_threadpool(_rows_from_upload, file.filename or "", raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Не мога да прочета файла: {str(e)[:140]}")

    cols: dict | None = None
    drow: int | None = None
    explicit = col_name >= 0                         # the wizard sent a real mapping
    if explicit:
        cols = {"code": col_code, "name": col_name, "qty": col_qty,
                "price": col_price, "barcode": col_barcode}
        drow = data_row or 1
    else:
        prof = await _profile_for(session, supplier)
        if prof is not None:
            cols, drow = _profile_cols(prof), prof.data_row

    parsed = await run_in_threadpool(_parse_rows, rows, supplier, cols, drow)
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове за приемане.")

    if save_profile and explicit and cols is not None and supplier.strip():
        await _save_profile(session, actor, supplier, cols, drow or 1)

    source = "csv" if (file.filename or "").lower().endswith(".csv") else "excel"
    return await _persist_batch(session, actor, supplier, supplier_id, depot_id, reference,
                                doc_date, source, parsed)


async def _persist_batch(session, actor, supplier, supplier_id, depot_id, reference, doc_date,
                         source, parsed: list[dict], zayavka_id: int | None = None) -> dict:
    """Create a DeliveryBatch + its lines; each NEW line gets a linked intake draft so it surfaces
    on Приемане for enrichment. Shared by the file, edited-rows and OCR create paths."""
    b = DeliveryBatch(supplier=(supplier or "").strip(), supplier_id=supplier_id or None,
                      depot_id=depot_id or 1, reference=(reference or "").strip(),
                      doc_date=(doc_date or "").strip(), source=source, status="draft",
                      zayavka_id=zayavka_id, created_by=actor.email)
    session.add(b)
    await session.flush()
    for p in parsed:
        ln = DeliveryLine(
            batch_id=b.id, supplier_code=p["supplier_code"], name=p["name"], qty=p["qty"],
            price=p["price"], barcode=p.get("barcode", ""), is_new=p["is_new"],
            match_code=p["match_code"], match_method=p["match_method"],
            match_confidence=p["match_confidence"], article_id=None, status="pending")
        session.add(ln)
        await session.flush()
        if p["is_new"]:
            d = IntakeDraft(code=p["supplier_code"], barcode=p.get("barcode", ""), name=p["name"],
                            status="new_from_delivery", approved_by="", delivery_line_id=ln.id)
            session.add(d)
            await session.flush()
            ln.intake_draft_id = d.id
    lines = await _lines(session, b.id)
    _recompute_status(b, lines)
    await session.commit()
    await session.refresh(b)
    await log_action(session, actor, "delivery.create", target=b.supplier or b.reference,
                     detail=f"batch={b.id} lines={len(parsed)} new={sum(1 for p in parsed if p['is_new'])}")
    return await _detail(session, b)


class RawLine(BaseModel):
    supplier_code: str = ""
    name: str = ""
    qty: float = 0.0
    price: float = 0.0
    barcode: str = ""


class FromRowsRequest(BaseModel):
    supplier: str = ""
    supplier_id: int | None = None
    depot_id: int = 1
    reference: str = ""
    doc_date: str = ""
    source: str = "photo"
    rows: list[RawLine]


@router.post("/from-rows", status_code=status.HTTP_201_CREATED)
async def create_from_rows(
    req: FromRowsRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a delivery from an already-extracted, staff-edited table (the OCR / manual path)."""
    rows = [r for r in req.rows if r.name.strip() or r.supplier_code.strip()]
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове.")
    parsed = [_match_row(req.supplier, r.supplier_code.strip(), r.name.strip(), r.qty, r.price,
                         r.barcode.strip()) for r in rows]
    return await _persist_batch(session, actor, req.supplier, req.supplier_id, req.depot_id,
                                req.reference, req.doc_date, req.source or "photo", parsed)


@router.post("/ocr")
async def ocr_photo(
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
):
    """Extract a table from a PHOTO of a paper delivery note (the 'Нямам файл, само хартия' path).
    Returns raw rows for staff to CORRECT before creating the delivery (never trusted as-is).
    Barcodes are decoded from the image (pyzbar), never OCR'd as digits. OCR deps are optional —
    if the server doesn't have them installed the endpoint reports 503 cleanly."""
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)   # bounded read — don't buffer an oversized upload
    _check_size(raw)
    try:
        rows, note = await run_in_threadpool(_ocr_extract, raw)
    except _OcrUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Разчитането на снимката се провали: {str(e)[:140]}")
    return {"rows": rows, "note": note}


class _OcrUnavailable(RuntimeError):
    pass


def _ocr_extract(image_bytes: bytes) -> tuple[list[dict], str]:
    """Best-effort table extraction from a photo via img2table + a Cyrillic OCR backend.
    Returns (raw rows, human note). Raises _OcrUnavailable if the optional deps aren't installed."""
    try:
        from img2table.document import Image as I2TImage
        try:
            from img2table.ocr import RapidOCR as _OCR  # ONNX, Cyrillic, CPU-friendly (preferred)
            ocr = _OCR()
        except Exception:
            from img2table.ocr import TesseractOCR as _OCR  # apt: tesseract-ocr-bul
            ocr = _OCR(lang="bul")
    except Exception as e:
        raise _OcrUnavailable(
            "OCR не е инсталиран на сървъра (img2table + rapidocr). Използвай Excel/CSV засега.") from e

    doc = I2TImage(io.BytesIO(image_bytes))
    tables = doc.extract_tables(ocr=ocr, implicit_rows=True, borderless_tables=True,
                                min_confidence=40)
    rows: list[dict] = []
    for tb in tables or []:
        for row in tb.content.values():
            cells = [(c.value or "").strip() if c else "" for c in row]
            cells = [c for c in cells if c]
            if not cells:
                continue
            # heuristic: first alphanumeric-ish = code, longest = name, numerics = qty/price
            nums = [DP.to_float(c) for c in cells if any(ch.isdigit() for ch in c)]
            name = max(cells, key=len)
            code = next((c for c in cells if c != name and any(ch.isalnum() for ch in c)), "")
            qty = nums[0] if nums else 0.0
            price = nums[-1] if len(nums) > 1 else 0.0
            rows.append({"supplier_code": code, "name": name, "qty": qty, "price": price, "barcode": ""})

    # decode any barcodes visible in the photo (reliable, unlike OCR'ing the digits)
    barcodes = _decode_barcodes(image_bytes)
    note = (f"Разчетени {len(rows)} реда. Провери и коригирай — разпознаването от снимка не е точно."
            + (f" Намерени баркодове: {len(barcodes)}." if barcodes else ""))
    return rows, note


def _decode_barcodes(image_bytes: bytes) -> list[str]:
    try:
        from PIL import Image as PILImage
        from pyzbar.pyzbar import decode
    except Exception:
        return []
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        return [d.data.decode("utf-8", "ignore") for d in decode(img)]
    except Exception:
        return []


# ------------------------------------------------------------------ list + detail ---
@router.get("")
async def list_deliveries(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    batches = list((await session.execute(
        select(DeliveryBatch).order_by(DeliveryBatch.created_at.desc()).limit(100))).scalars().all())
    # line counts per batch in one grouped query
    counts = dict((bid, (tot, new)) for bid, tot, new in (await session.execute(
        select(DeliveryLine.batch_id, func.count(), func.sum(func.cast(DeliveryLine.is_new, Integer)))
        .group_by(DeliveryLine.batch_id))).all())
    out = []
    for b in batches:
        tot, new = counts.get(b.id, (0, 0))
        out.append({
            "id": b.id, "supplier": b.supplier, "reference": b.reference, "doc_date": b.doc_date,
            "source": b.source, "status": b.status, "storeload_id": b.storeload_id,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "total": tot or 0, "new": int(new or 0),
        })
    return {"deliveries": out}


@router.get("/{batch_id}")
async def get_delivery(
    batch_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    return await _detail(session, b)


# ------------------------------------------------------------------ scan-confirm ---
class ScanRequest(BaseModel):
    barcode: str


def _set_line_status(ln: DeliveryLine) -> None:
    # scan-confirm targets the RECEIVED qty. getattr: this helper is also driven with
    # line-shaped stubs (tests) that predate defect_qty — a missing attr means no defects.
    q = (ln.qty or 0) - (getattr(ln, "defect_qty", 0) or 0)
    if ln.scanned_qty == 0:
        ln.status = "pending"
    elif math.isclose(ln.scanned_qty, q, abs_tol=1e-9):
        ln.status = "confirmed"
    elif q != int(q) and math.floor(q) <= ln.scanned_qty <= math.ceil(q):
        # a fractional-unit line (e.g. 2.5 kg loose goods) can't be piece-scanned to an exact count,
        # so an integer scan count would never equal q and the line would block finalise forever.
        # A scan of the whole-number part confirms it — but BOUNDED above, or a runaway camera
        # tally of 40 on a 2.5 kg line would still read as confirmed.
        ln.status = "confirmed"
    else:
        ln.status = "mismatch"       # over- or under-count on a piece-goods line


async def _bump_scan(session: AsyncSession, ln: DeliveryLine, n: float) -> None:
    """Increment scanned_qty in SQL, never in Python. Two workers on one pallet were doing an
    unlocked read-modify-write (both read 3, both write 4) and silently losing beeps."""
    await session.execute(
        update(DeliveryLine).where(DeliveryLine.id == ln.id)
        .values(scanned_qty=DeliveryLine.scanned_qty + n))
    await session.refresh(ln)
    _set_line_status(ln)


def _candidate_lines(lines: list[DeliveryLine], code: str) -> list[DeliveryLine]:
    """Every line a scanned code could belong to — by its own barcode first, else by resolving the
    EAN through the catalogue to our code. A list, not the first hit: the same article legitimately
    appears on two rows (two lots at two prices)."""
    cands = [x for x in lines if x.barcode and x.barcode == code]
    if not cands:
        our = DP.barcode_index()["ean_to_code"].get(code)
        if our:
            cands = [x for x in lines if x.match_code and x.match_code == our]
    return cands


def _distribute(cands: list[DeliveryLine], n: float) -> list[tuple[DeliveryLine, float]]:
    """Spread n scanned units across lines carrying the same code, filling each to its RECEIVED
    target (qty - defect) before spilling into the next. Without this every beep lands on row 1,
    which over-scans into mismatch while row 2 sits at 0 — and both block finalise."""
    out: list[tuple[DeliveryLine, float]] = []
    left = n
    for x in cands:
        if left <= 0:
            break
        room = ((x.qty or 0) - (x.defect_qty or 0)) - (x.scanned_qty or 0)
        if room <= 0:
            continue
        take = min(left, room)
        out.append((x, take))
        left -= take
    if left > 0:
        # the surplus belongs on the last candidate — MERGED into its take, not appended as a
        # second entry, or one over-scan reports twice and the first report reads 'confirmed'
        if out and out[-1][0] is cands[-1]:
            out[-1] = (out[-1][0], out[-1][1] + left)
        else:
            out.append((cands[-1], left))
    return out


async def _guard_not_finalised(session: AsyncSession, batch_id: int) -> None:
    """Refuse mutations to a delivery that's already been sent to Barsy (local record must not drift
    from what Barsy recorded)."""
    b = await session.get(DeliveryBatch, batch_id)
    if b and b.status in ("finalised", "finalising"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката се финализира — не се променя.")


@router.post("/{batch_id}/scan")
async def scan(
    batch_id: int, req: ScanRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Register one scan of a barcode. Increments the matching line's scanned_qty and recolours it.
    If the barcode belongs to no line, returns unknown=True + the new lines (to offer 'attach')."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status in ("finalised", "finalising"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката е финализирана или се финализира.")
    code = (req.barcode or "").strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празен баркод.")
    lines = await _lines(session, b.id)

    cands = _candidate_lines(lines, code)
    if not cands:
        new_lines = [_line_out(x) for x in lines if x.is_new]
        return {"matched": False, "unknown": True, "barcode": code, "new_lines": new_lines}

    # one unit, but still pick the line with room — the same article can sit on two rows
    ln, take = _distribute(cands, 1)[0]
    await _bump_scan(session, ln, take)
    await session.commit()
    return {"matched": True, "line": _line_out(ln)}


class AttachRequest(BaseModel):
    barcode: str


@router.post("/{batch_id}/lines/{line_id}/attach-barcode")
async def attach_barcode(
    batch_id: int, line_id: int, req: AttachRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Attach a scanned (previously-unknown) barcode to a NEW product line, and count that scan.
    Also writes the barcode onto the line's linked intake draft so enrichment inherits it."""
    ln = await session.get(DeliveryLine, line_id)
    if not ln or ln.batch_id != batch_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    await _guard_not_finalised(session, batch_id)
    if not ln.is_new:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Само нов продукт може да получи баркод тук.")
    ln.barcode = (req.barcode or "").strip()
    await _bump_scan(session, ln, 1)
    if ln.intake_draft_id:
        d = await session.get(IntakeDraft, ln.intake_draft_id)
        if d and not d.barcode:
            d.barcode = ln.barcode
    await session.commit()
    return {"ok": True, "line": _line_out(ln)}


# ------------------------------------------------------------------ edit / delete line ---
class LinePatch(BaseModel):
    name: str | None = None
    qty: float | None = None
    price: float | None = None
    barcode: str | None = None
    supplier_code: str | None = None


@router.patch("/{batch_id}/lines/{line_id}")
async def edit_line(
    batch_id: int, line_id: int, req: LinePatch,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Correct a line (qty/price/name/barcode/code) — also the manual-fix path for OCR drafts."""
    ln = await session.get(DeliveryLine, line_id)
    if not ln or ln.batch_id != batch_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    await _guard_not_finalised(session, batch_id)
    for f in ("name", "qty", "price", "barcode", "supplier_code"):
        v = getattr(req, f)
        if v is not None:
            setattr(ln, f, v)
    _set_line_status(ln)
    await session.commit()
    return {"ok": True, "line": _line_out(ln)}


@router.delete("/{batch_id}")
async def delete_delivery(
    batch_id: int,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Discard a delivery that hasn't been finalised (+ its lines + their staged drafts)."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status in ("finalised", "finalising"):
        # deleting mid-write would land the goods in Barsy with no local record at all
        raise HTTPException(status.HTTP_409_CONFLICT, "Финализирана доставка не се изтрива.")
    lines = await _lines(session, batch_id)
    draft_ids = [ln.intake_draft_id for ln in lines if ln.intake_draft_id]
    if draft_ids:
        await session.execute(delete(IntakeDraft).where(
            IntakeDraft.id.in_(draft_ids), IntakeDraft.status == "new_from_delivery"))
    await session.execute(delete(DeliveryLine).where(DeliveryLine.batch_id == batch_id))
    await session.delete(b)
    await session.commit()
    await log_action(session, actor, "delivery.delete", target=b.supplier or str(batch_id))
    return {"ok": True}


# ------------------------------------------------------------------ finalise → storeload ---
class FinaliseRequest(BaseModel):
    confirm: bool = False
    force: bool = False        # allow finalise despite scan mismatches (records a warning)


@router.post("/{batch_id}/finalise")
async def finalise(
    batch_id: int, req: FinaliseRequest,
    actor: User = Depends(current_owner),      # ERP write — owner/manager only
    session: AsyncSession = Depends(get_async_session),
):
    """Send the whole delivery to Barsy as ONE storeload. Gate: every line must have a Barsy
    article_id (new products enriched + created via Приемане). Gated by DELIVERY_BARSY_LIVE."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status == "finalised":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Вече е финализирана (storeload {b.storeload_id}).")
    lines = await _lines(session, batch_id)
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове.")
    # every line must carry a real, positive quantity before it becomes a Barsy goods-in row
    bad_qty = [ln for ln in lines if ln.qty <= 0]
    if bad_qty:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{len(bad_qty)} реда без валидно количество — поправи ги преди финализиране.")
    # gate 1: every NEW product must have been enriched + created in Barsy (article_id written back)
    new_missing = [ln for ln in lines if ln.is_new and ln.article_id is None]
    if new_missing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Има {len(new_missing)} нови продукта, които не са добавени. "
                            "Отиди на Приемане, за да ги завършиш.")
    # resolve existing lines' Barsy article_id from the catalogue (by our code) — only the codes we need
    to_resolve = [ln for ln in lines if ln.article_id is None]
    if to_resolve:
        try:
            cmap = await run_in_threadpool(_catalog_ids_for_codes, {ln.match_code for ln in to_resolve})
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy каталог: {str(e)[:160]}")
        for ln in to_resolve:
            aid = cmap.get(ln.match_code)
            if aid is not None:
                ln.article_id = aid
    still_missing = [ln for ln in lines if ln.article_id is None]
    if still_missing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{len(still_missing)} продукта не са намерени в Barsy (по код). "
                            "Провери кода или ги създай в Приемане.")
    # scan gate: EVERY line must be scan-confirmed (scanned == claimed). Unscanned (pending) lines
    # are just as unverified as mismatched ones — both block unless the owner explicitly forces.
    unconfirmed = [ln for ln in lines if ln.status != "confirmed"]
    warnings: list[str] = []
    if unconfirmed and not req.force:
        pend = sum(1 for ln in unconfirmed if ln.status == "pending")
        mis = len(unconfirmed) - pend
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{len(unconfirmed)} реда не са потвърдени при сканиране "
                            f"({pend} непроверени, {mis} с разлика). Провери или потвърди с force=true.")
    if unconfirmed:
        warnings.append(f"{len(unconfirmed)} реда финализирани без пълно сканиране.")

    settings = get_settings()
    # deterministic doc_num per batch → if the write is retried, Barsy rejects the duplicate doc_num
    # instead of recording a second goods-in.
    doc_num = ((b.reference or "DZ").strip().replace(" ", "-")[:36] or "DZ") + f"-B{b.id}"
    b.doc_num = doc_num                      # persisted so /reconcile can probe Barsy for it
    b.finalise_started_at = _dt.datetime.now(_dt.UTC)
    await session.commit()
    payload = {
        "store_load": {
            "depot_id": b.depot_id, "doc_date": b.doc_date or time.strftime("%Y-%m-%d"),
            "doc_num": doc_num, "operation_type": 1, "doc_type_id": 1,
            # prices are EUR + VAT-inclusive — see barsy_currency_id / barsy_price_mode (COGS correctness)
            "currency_id": settings.barsy_currency_id, "price_mode": settings.barsy_price_mode,
        },
        # received = claimed - defective; a fully-defective line is not booked at all
        "articles": [{"article_id": int(ln.article_id),  # type: ignore[arg-type]  # SQLAlchemy/fastapi-users stub gap, not a bug
                      "amount": str((ln.qty or 0) - (ln.defect_qty or 0)),
                      "current_price": str(ln.price)}
                     for ln in lines if (ln.qty or 0) - (ln.defect_qty or 0) > 0],
        "action_type": "save_and_close",
    }
    if b.supplier_id:
        payload["store_load"]["supplier_id"] = b.supplier_id  # type: ignore[index]  # SQLAlchemy/fastapi-users stub gap, not a bug

    live = settings.delivery_barsy_live and req.confirm
    if not live:
        if not settings.delivery_barsy_live:
            warnings.append("DELIVERY_BARSY_LIVE е изключен — това е проба (нищо не се записва).")
        elif not req.confirm:
            warnings.append("Потвърди с confirm=true, за да се запише в Barsy.")
        await log_action(session, actor, "delivery.finalise.dry_run", target=b.supplier or b.reference,
                         detail=f"batch={b.id} lines={len(lines)}")
        return {"ok": True, "dry_run": True, "storeload_payload": payload, "warnings": warnings}

    # LIVE. Atomic claim FIRST — flip to 'finalising' only if not already finalised/finalising.
    # Blocks the double-finalise race (double-click / two tabs) BEFORE the real Barsy stock write,
    # mirroring intake.push_to_barsy's approved→pushing claim.
    claim = await session.execute(
        update(DeliveryBatch).where(DeliveryBatch.id == batch_id,
                                    DeliveryBatch.status.notin_(("finalised", "finalising")))
        .values(status="finalising"))
    await session.commit()
    if claim.rowcount == 0:  # type: ignore[attr-defined]  # SQLAlchemy/fastapi-users stub gap, not a bug
        await session.refresh(b)
        if b.status == "finalised":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Вече е финализирана (storeload {b.storeload_id}).")
        raise HTTPException(status.HTTP_409_CONFLICT, "Финализирането вече тече.")

    # resolve supplier_id if we only have the name, then one storeload
    if b.supplier_id is None and b.supplier.strip():
        try:
            sid = await run_in_threadpool(DP.resolve_supplier_id, b.supplier.strip())
            if sid:
                b.supplier_id = sid
                payload["store_load"]["supplier_id"] = sid  # type: ignore[index]  # SQLAlchemy/fastapi-users stub gap, not a bug
        except Exception:
            warnings.append("Доставчикът не бе намерен в Barsy — без supplier_id.")
    try:
        out = await run_in_threadpool(barsy.call, "Storeloads_create", payload)
    except barsy.BarsyUncertainError as e:
        # The write may have landed. Do NOT release the claim — a blind retry would book the
        # goods twice. Leave it 'finalising' for the owner-only /reconcile probe.
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Barsy не отговори ясно — доставката може да е записана. "
                            f"Използвай 'Свери' ({doc_num}), не повтаряй. {str(e)[:120]}")
    except Exception as e:
        b.status = "ready"            # release the claim so it can be retried
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy (доставка): {str(e)[:180]}")
    b.storeload_id = extract_id(out, ("storeload_id", "id"))   # a storeload, never an article
    b.status = "finalised"
    b.finalised_at = _dt.datetime.now(_dt.UTC)
    await _flip_zayavka_received(session, b)
    await session.commit()
    await log_action(session, actor, "delivery.finalise", target=b.supplier or b.reference,
                     detail=f"batch={b.id} storeload={b.storeload_id} lines={len(lines)}")
    return {"ok": True, "dry_run": False, "storeload_id": b.storeload_id,
            "storeload_payload": payload, "warnings": warnings}


# ------------------------------------------------------------------ camera batch scan ---
class ScanBatchRequest(BaseModel):
    """One commit from the CAMERA scanner: barcode → how many physical instances were locked
    in view. The camera sees many codes at once, and several items of the same code appear as
    separate bounding boxes — so a whole tally arrives in one call instead of replaying the
    single-scan endpoint N times (which would repaint the table N times and interleave badly
    with a second scanner on the same delivery)."""
    counts: dict[str, int]


@router.post("/{batch_id}/scan-batch")
async def scan_batch(
    batch_id: int, req: ScanBatchRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Apply a camera tally atomically. Matching mirrors /scan exactly — a line already
    carrying the barcode wins, else the EAN resolves through the catalogue to our code and
    that code's line. Unknown barcodes are returned (NOT counted) so the UI can run the
    tie-to-new-product dialog per code; bounds are enforced so a runaway tracker cannot book
    an absurd quantity."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status in ("finalised", "finalising"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката е финализирана или се финализира.")
    counts = {(k or "").strip(): v for k, v in req.counts.items() if (k or "").strip()}
    if not counts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празно сканиране.")
    if len(counts) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Твърде много кодове в едно сканиране (макс. 200).")
    for code, n in counts.items():
        if n < 1 or n > 500:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Невалиден брой за {code} (1–500).")
    lines = await _lines(session, b.id)
    applied: list[dict] = []
    unknown: list[str] = []
    for code, n in counts.items():
        cands = _candidate_lines(lines, code)
        if not cands:
            unknown.append(code)
            continue
        # fill each same-code line to its target before spilling into the next
        for ln, take in _distribute(cands, n):
            await _bump_scan(session, ln, take)
            applied.append({"barcode": code, "count": take, "line": _line_out(ln)})
    await session.commit()
    return {"applied": applied, "unknown": unknown, "summary": _batch_summary(b, lines)}


# ------------------------------------------------------------------ import profiles ---
def _profile_cols(p: SupplierImportProfile) -> dict:
    cols = {"code": p.col_code, "name": p.col_name, "qty": p.col_qty, "price": p.col_price}
    if p.col_barcode is not None and p.col_barcode >= 0:
        cols["barcode"] = p.col_barcode
    return cols


def _profile_out(p: SupplierImportProfile) -> dict:
    return {"id": p.id, "supplier": p.supplier, "data_row": p.data_row,
            "col_code": p.col_code, "col_name": p.col_name, "col_qty": p.col_qty,
            "col_price": p.col_price, "col_barcode": p.col_barcode,
            "updated_by": p.updated_by,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


async def _profile_for(session: AsyncSession, supplier: str) -> SupplierImportProfile | None:
    key = norm(supplier)
    if not key:
        return None
    return (await session.execute(
        select(SupplierImportProfile).where(SupplierImportProfile.supplier_norm == key)
        .limit(1))).scalars().first()


async def _save_profile(session: AsyncSession, actor: User, supplier: str,
                        cols: dict, data_row: int) -> SupplierImportProfile:
    key = norm(supplier)
    p = await _profile_for(session, supplier)
    if p is None:
        p = SupplierImportProfile(supplier=supplier.strip(), supplier_norm=key)
        session.add(p)
    p.supplier = supplier.strip()
    p.data_row = max(1, int(data_row))
    p.col_code = int(cols.get("code", -1))
    p.col_name = int(cols.get("name", 1))
    p.col_qty = int(cols.get("qty", -1))
    p.col_price = int(cols.get("price", -1))
    p.col_barcode = int(cols.get("barcode", -1))
    p.updated_by = actor.email
    await session.commit()
    await session.refresh(p)
    return p


def _col_letters(n: int) -> list[str]:
    out = []
    for i in range(n):
        s, x = "", i
        while True:
            s = chr(ord("A") + x % 26) + s
            x = x // 26 - 1
            if x < 0:
                break
        out.append(s)
    return out


@profiles_router.post("/preview")
async def preview_delivery(
    file: UploadFile = File(...),
    supplier: str = Form(""),
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Read the first rows WITHOUT creating anything, so the wizard can show a grid and let the
    user map columns + pick the start row. Returns the raw rows (as strings), an auto-detected
    suggestion, and this supplier's saved profile if one exists."""
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    _check_size(raw)
    try:
        rows = await run_in_threadpool(_rows_from_upload, file.filename or "", raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Не мога да прочета файла: {str(e)[:140]}")
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празен файл.")

    preview = rows[:20]
    ncol = max((len(r) for r in preview), default=0)
    grid = [[("" if c is None else str(c)) for c in (r + [None] * (ncol - len(r)))] for r in preview]

    stripped = DP.strip_leading_blank(rows)
    leading_blank = len(rows) - len(stripped)
    detected = DP.detect_columns(stripped[0]) if stripped else {}
    suggested = {
        "code": detected.get("code", -1), "name": detected.get("name", -1),
        "qty": detected.get("qty", -1), "price": detected.get("price", -1),
        "barcode": detected.get("barcode", -1),
        # 1-based first product row in ORIGINAL coordinates
        "data_row": leading_blank + (2 if detected else 1),
    }
    prof = await _profile_for(session, supplier)
    return {"columns": _col_letters(ncol), "rows": grid,
            "suggested": suggested, "profile": _profile_out(prof) if prof else None}


@profiles_router.get("/import-profiles")
async def list_import_profiles(
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    rows = list((await session.execute(
        select(SupplierImportProfile).order_by(SupplierImportProfile.supplier))).scalars().all())
    return {"profiles": [_profile_out(p) for p in rows]}


@profiles_router.delete("/import-profiles/{profile_id}")
async def delete_import_profile(
    profile_id: int,
    actor: User = Depends(current_owner),   # a remembered format is shared config → owner/manager
    session: AsyncSession = Depends(get_async_session),
):
    p = await session.get(SupplierImportProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Форматът не е намерен.")
    await session.delete(p)
    await session.commit()
    await log_action(session, actor, "delivery.import_profile.delete", target=p.supplier)
    return {"ok": True}


# ------------------------------------------------------------------ receive from a Заявка ---
async def _flip_zayavka_received(session: AsyncSession, b: DeliveryBatch) -> None:
    """When a delivery made from a заявка finalises, mark that заявка 'received' — the receiving
    step is what Barsy calls converting a request to „Заредени"."""
    if not b.zayavka_id:
        return
    z = await session.get(Zayavka, b.zayavka_id)
    if z is not None and z.status != "received":
        z.status = "received"


def _parsed_from_zayavka_lines(supplier: str, lines: list) -> list[dict]:
    """Build the same normalised line dicts the sheet parser produces, from заявка lines. Prefer
    the supplier's code for matching, fall back to our code; carry the barcode if the line has one."""
    out = []
    for ln in lines:
        code = (ln.supplier_code or ln.code or "").strip()
        out.append(_match_row(supplier, code, ln.name or "", ln.qty or 0.0,
                              ln.delivery_price or 0.0, (ln.barcode or "").strip()))
    return out


@profiles_router.get("/zayavki-open")
async def list_open_zayavki(
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Sent заявки that can be received — the picker in the 'От заявка' source of the wizard."""
    rows = list((await session.execute(
        select(Zayavka).where(Zayavka.status == "sent")
        .order_by(Zayavka.created_at.desc()).limit(100))).scalars().all())
    counts = dict((zid, n) for zid, n in (await session.execute(
        select(ZayavkaLine.zayavka_id, func.count()).group_by(ZayavkaLine.zayavka_id))).all())
    return {"zayavki": [{
        "id": z.id, "supplier": z.supplier, "reference": "",
        "created_at": z.created_at.isoformat() if z.created_at else None,
        "lines": int(counts.get(z.id, 0)),
    } for z in rows]}


@profiles_router.post("/from-zayavka/{zayavka_id}", status_code=status.HTTP_201_CREATED)
async def create_from_zayavka(
    zayavka_id: int,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Start receiving a delivery from a sent заявка: build a draft batch from its lines and link
    it back, so finalising flips the заявка to 'received'. Refuses an already-received/cancelled one."""
    z = await session.get(Zayavka, zayavka_id)
    if z is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявката не е намерена.")
    if z.status in ("received", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Заявката вече е приета или отказана.")
    lines = list((await session.execute(
        select(ZayavkaLine).where(ZayavkaLine.zayavka_id == z.id)
        .order_by(ZayavkaLine.id))).scalars().all())
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заявката няма редове.")
    parsed = await run_in_threadpool(_parsed_from_zayavka_lines, z.supplier, lines)
    return await _persist_batch(session, actor, z.supplier, z.supplier_id or 0, 1, "", "",
                                "zayavka", parsed, zayavka_id=z.id)


# ------------------------------------------------------------------ defectives + claims (D3) ---
# A worker sets aside a defective unit at goods-in. The owner's rule: it is NOT booked into stock,
# and the supplier owes a replacement / credit / refund. So a line carries defect_qty (received =
# qty − defect_qty; the storeload books only good units) and each defect opens a SupplierClaim that
# Рекламации tracks to resolution. Static /claims* routes live on profiles_router (included before
# the main router) so GET /{batch_id} does not shadow them; /{batch_id}/… defect route is safe on
# the main router because its first segment is dynamic.
_CLAIM_STATES = {"pending", "replacement", "credit", "refund", "rejected"}


def _claim_out(c: SupplierClaim) -> dict:
    return {
        "id": c.id, "batch_id": c.batch_id, "line_id": c.line_id, "supplier": c.supplier,
        "supplier_code": c.supplier_code, "name": c.name, "qty": c.qty, "price": c.price,
        "note": c.note, "status": c.status, "resolution_note": c.resolution_note,
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
    }


class DefectRequest(BaseModel):
    qty: float
    note: str = ""
    # what the caller believed the current defect count was. Absolute set is the right
    # semantics ("2 of these are broken"), but two workers each marking one broken unit
    # would otherwise land on 1 and lose the other. Optional: older clients still work.
    expected_defect_qty: float | None = None


@router.post("/{batch_id}/lines/{line_id}/defect")
async def mark_defect(
    batch_id: int, line_id: int, req: DefectRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Set aside N units of a line as defective. received = qty − defect_qty, so the line recolours
    against the received target and the eventual storeload books only good units. Opens (or updates)
    the line's pending SupplierClaim; qty=0 clears the defect and removes a still-pending claim.
    Refuses a finalised delivery and a defect count above the delivered qty."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status in ("finalised", "finalising"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката се финализира — не се променя.")
    ln = await session.get(DeliveryLine, line_id)
    if not ln or ln.batch_id != batch_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    qty = float(req.qty or 0)
    if qty < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дефектното количество не може да е отрицателно.")
    if qty > (ln.qty or 0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дефектното количество надвишава доставеното.")
    if (req.expected_defect_qty is not None
            and abs((ln.defect_qty or 0) - req.expected_defect_qty) > 1e-9):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Междувременно е отбелязано друго количество ({ln.defect_qty}). "
                            "Обнови и опитай пак.")

    ln.defect_qty = qty
    _set_line_status(ln)   # the received target shrank — recolour against qty − defect

    existing = (await session.execute(
        select(SupplierClaim).where(SupplierClaim.line_id == ln.id,
                                    SupplierClaim.status == "pending"))).scalars().first()
    claim_id: int | None = None
    note = (req.note or "").strip()
    if qty == 0:
        if existing is not None:
            await session.delete(existing)   # a mis-tap is undone, not left as a phantom claim
    elif existing is not None:
        existing.qty = qty
        existing.price = ln.price or 0.0
        existing.note = note
        claim_id = existing.id
    else:
        claim = SupplierClaim(
            batch_id=batch_id, line_id=ln.id, supplier=b.supplier or "",
            supplier_code=ln.supplier_code or "", name=ln.name or "",
            qty=qty, price=ln.price or 0.0, note=note, status="pending",
            created_by=actor.email)
        session.add(claim)
        await session.flush()
        claim_id = claim.id

    await log_action(session, actor, "delivery.defect", target=b.supplier or b.reference,
                     detail=f"batch={batch_id} line={line_id} qty={qty}")
    await session.commit()
    await session.refresh(ln)
    return {"ok": True, "line": _line_out(ln), "claim_id": claim_id}


@profiles_router.get("/claims")
async def list_claims(
    state: str = "",
    supplier: str = "",
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """The Рекламации list. state: '' = all, 'open' = pending only, 'resolved' = everything else,
    or an exact status. Optionally narrow to one supplier."""
    q = select(SupplierClaim)
    if state == "open":
        q = q.where(SupplierClaim.status == "pending")
    elif state == "resolved":
        q = q.where(SupplierClaim.status != "pending")
    elif state:
        q = q.where(SupplierClaim.status == state)
    if supplier:
        q = q.where(SupplierClaim.supplier == supplier)
    rows = list((await session.execute(
        q.order_by(SupplierClaim.created_at.desc()).limit(500))).scalars().all())
    return {"claims": [_claim_out(c) for c in rows]}


@profiles_router.get("/claims-open")
async def open_claims_for_supplier(
    supplier: str = "",
    _: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Outstanding (pending) claims — the banner on a delivery keyed by supplier. No supplier = all."""
    q = select(SupplierClaim).where(SupplierClaim.status == "pending")
    if supplier:
        q = q.where(SupplierClaim.supplier == supplier)
    rows = list((await session.execute(
        q.order_by(SupplierClaim.created_at.desc()))).scalars().all())
    return {"count": len(rows), "supplier": supplier, "claims": [_claim_out(c) for c in rows]}


class ResolveRequest(BaseModel):
    status: str
    note: str = ""


@profiles_router.post("/claims/{claim_id}/resolve")
async def resolve_claim(
    claim_id: int, req: ResolveRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Record what the supplier agreed to: замяна / кредит / връщане на пари / отказан. Setting it
    back to 'pending' reopens the claim (clears resolved_at)."""
    c = await session.get(SupplierClaim, claim_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Рекламацията не е намерена.")
    if req.status not in _CLAIM_STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден статус.")
    c.status = req.status
    c.resolution_note = (req.note or "").strip()
    c.resolved_at = None if req.status == "pending" else _dt.datetime.now(_dt.UTC)
    await log_action(session, actor, "delivery.claim.resolve", target=c.supplier,
                     detail=f"claim={claim_id} -> {req.status}")
    await session.commit()
    await session.refresh(c)
    return {"ok": True, "claim": _claim_out(c)}


# ------------------------------------------------------------------ stuck-finalise reconcile (H1) ---
# A delivery whose Barsy write timed out stays 'finalising' on purpose: releasing it would invite a
# retry that books the goods a second time. The way out is to ASK Barsy whether it has the doc_num
# we persisted before the write, and act on the answer — never to guess.


def _storeload_doc_num(row: dict) -> str:
    """doc_num out of a Storeloads_getlist row, whatever Barsy called it this time."""
    for k in ("doc_num", "docNum", "document_number", "doc_number", "number"):
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _find_storeload_by_doc_num(doc_num: str, depot_id: int) -> tuple[bool, int | None]:
    """(found, storeload_id) for a doc_num. Runs in a threadpool — plain sync Barsy calls.
    A read, so barsy.call may safely retry it."""
    out = barsy.call("Storeloads_getlist", {"depot_id": depot_id}, is_read=True)
    rows = out if isinstance(out, list) else (out.get("data") or out.get("storeloads") or [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _storeload_doc_num(row) == doc_num:
            return True, extract_id(row, ("storeload_id", "id"))
    return False, None


@profiles_router.get("/stuck")
async def list_stuck_finalising(
    _: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Deliveries stranded mid-finalise. Each needs a /reconcile decision before it can move."""
    rows = list((await session.execute(
        select(DeliveryBatch).where(DeliveryBatch.status == "finalising")
        .order_by(DeliveryBatch.id))).scalars().all())
    return {"stuck": [{
        "id": b.id, "supplier": b.supplier, "reference": b.reference, "doc_num": b.doc_num,
        "finalise_started_at": b.finalise_started_at.isoformat() if b.finalise_started_at else None,
    } for b in rows]}


@router.post("/{batch_id}/reconcile")
async def reconcile_finalise(
    batch_id: int,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Resolve a delivery stranded in 'finalising' by asking Barsy whether it holds our doc_num.

    Found     -> adopt the storeload id, mark finalised, flip the заявка. No second write.
    Not found -> the goods were never booked; release the claim back to 'ready' so it can be retried.
    Owner-only: it decides whether stock exists."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status == "finalised":
        return {"ok": True, "action": "already", "storeload_id": b.storeload_id}
    if b.status != "finalising":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Тази доставка не е блокирала при финализиране.")
    if not b.doc_num:
        # stranded before doc_num was persisted (pre-H1 row) — nothing was sent, safe to release
        b.status = "ready"
        await log_action(session, actor, "delivery.reconcile.released", target=b.supplier,
                         detail=f"batch={batch_id} no doc_num")
        await session.commit()
        return {"ok": True, "action": "released", "reason": "no doc_num recorded"}

    try:
        found, sid = await run_in_threadpool(_find_storeload_by_doc_num, b.doc_num, b.depot_id or 1)
    except Exception as e:
        # Barsy still unreachable — leave it stuck. Guessing here is exactly the double-booking risk.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Barsy не отговори — доставката остава за сверяване. {str(e)[:160]}")

    if found:
        b.storeload_id = sid
        b.status = "finalised"
        b.finalised_at = _dt.datetime.now(_dt.UTC)
        await _flip_zayavka_received(session, b)
        await log_action(session, actor, "delivery.reconcile.adopted", target=b.supplier,
                         detail=f"batch={batch_id} doc_num={b.doc_num} storeload={sid}")
        await session.commit()
        return {"ok": True, "action": "adopted", "storeload_id": sid, "doc_num": b.doc_num}

    b.status = "ready"
    await log_action(session, actor, "delivery.reconcile.released", target=b.supplier,
                     detail=f"batch={batch_id} doc_num={b.doc_num} not in Barsy")
    await session.commit()
    return {"ok": True, "action": "released", "doc_num": b.doc_num}


# ------------------------------------------------------------------ take a scan back (H2/A4) ---
# scanned_qty could only ever go UP, and the natural working order is scan-everything-THEN-inspect:
# a worker scans 10, finds 2 cracked, taps Дефектен 2 — the received target drops to 8 while scanned
# stays 10, so the line is permanently red and finalise is blocked. A single double-beep does the
# same. This is the way back.


def _scan_adjust_body(ln: DeliveryLine, delta: float | None, reset: bool) -> float:
    """The new scanned_qty. Never negative — an over-correction should land on zero, not below it."""
    if reset:
        return 0.0
    return max(0.0, (ln.scanned_qty or 0) + (delta or 0))


class ScanAdjustRequest(BaseModel):
    delta: float | None = None      # negative to take scans back; positive to add by hand
    reset: bool = False             # clear the line's tally outright


@router.post("/{batch_id}/lines/{line_id}/scan-adjust")
async def scan_adjust(
    batch_id: int, line_id: int, req: ScanAdjustRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Correct a line's scan tally: −1 for a double-beep, reset to recount a box from scratch.
    Applied in SQL so it cannot lose a concurrent beep from a second phone."""
    b = await session.get(DeliveryBatch, batch_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Доставката не е намерена.")
    if b.status in ("finalised", "finalising"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката е финализирана или се финализира.")
    ln = await session.get(DeliveryLine, line_id)
    if not ln or ln.batch_id != batch_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    if req.delta is None and not req.reset:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма промяна.")

    target = _scan_adjust_body(ln, req.delta, req.reset)
    # absolute set (the caller stated the intended end state), but computed from the row we just
    # read — the clamp at zero is what makes a blind "−1" safe to tap repeatedly
    await session.execute(
        update(DeliveryLine).where(DeliveryLine.id == line_id).values(scanned_qty=target))
    await session.refresh(ln)
    _set_line_status(ln)
    await log_action(session, actor, "delivery.scan_adjust", target=b.supplier or b.reference,
                     detail=f"batch={batch_id} line={line_id} -> {target}")
    await session.commit()
    await session.refresh(ln)
    return {"ok": True, "line": _line_out(ln)}
