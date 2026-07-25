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
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import Integer, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import DeliveryBatch, DeliveryLine, IntakeDraft, User
from ..users import current_active_user, current_owner
from . import intake_delivery as D

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])


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
        return D._read_rows(raw)
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
    m = D._match_existing(supplier, code, name)
    if price <= 0 and m and m.get("delivery_price"):
        price = D._to_float(m["delivery_price"])
    if not barcode and m:
        # a known EAN for an existing product lets the scanner confirm it even if the doc had none
        eans = D._barcode_index()["code_to_eans"].get(m["code"]) or []
        barcode = eans[0] if eans else ""
    return {
        "supplier_code": code, "name": (m["name"] if m else name), "qty": qty, "price": price,
        "barcode": barcode, "is_new": m is None,
        "match_code": (m or {}).get("code", ""), "match_method": (m or {}).get("method", ""),
        "match_confidence": (m or {}).get("confidence", ""),
    }


def _parse_rows(rows: list[list], supplier: str) -> list[dict]:
    """Detect columns, match each data row → a normalised line dict (mirrors intake_delivery.parse)."""
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове в таблицата.")
    header = rows[0]
    cols = D._detect_columns(header)
    if not cols:
        cols = {"code": 0, "name": 1, "qty": 2, "price": 3}
        data_rows = rows
    else:
        data_rows = rows[1:]
    out = []
    for r in data_rows:
        def cell(field):
            j = cols.get(field)
            return r[j] if j is not None and j < len(r) else None
        nm = str(cell("name") or "").strip()
        code = str(cell("code") or "").strip()
        if not nm and not code:
            continue
        out.append(_match_row(supplier, code, nm, D._to_float(cell("qty")), D._to_float(cell("price"))))
    return out


# ------------------------------------------------------------------ serialisation ---
def _line_out(ln: DeliveryLine) -> dict:
    return {
        "id": ln.id, "supplier_code": ln.supplier_code, "name": ln.name, "qty": ln.qty,
        "price": ln.price, "barcode": ln.barcode, "is_new": ln.is_new, "match_code": ln.match_code,
        "match_method": ln.match_method, "match_confidence": ln.match_confidence,
        "article_id": ln.article_id, "scanned_qty": ln.scanned_qty, "status": ln.status,
        "intake_draft_id": ln.intake_draft_id,
    }


def _batch_summary(b: DeliveryBatch, lines: list[DeliveryLine]) -> dict:
    new_pending = sum(1 for ln in lines if ln.is_new and ln.article_id is None)
    return {
        "id": b.id, "supplier": b.supplier, "supplier_id": b.supplier_id, "depot_id": b.depot_id,
        "reference": b.reference, "doc_date": b.doc_date, "source": b.source, "status": b.status,
        "storeload_id": b.storeload_id, "created_by": b.created_by,
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
    if claim.rowcount == 0:
        return {"ready": True, "sent": False, "already": True}
    doc_num = ((b.reference or "DZ").strip().replace(" ", "-")[:36] or "DZ") + f"-B{b.id}"
    payload = {
        "store_load": {
            "depot_id": b.depot_id, "doc_date": b.doc_date or time.strftime("%Y-%m-%d"),
            "doc_num": doc_num, "operation_type": 1, "doc_type_id": 1,
        },
        "articles": [{"article_id": int(ln.article_id), "amount": str(ln.qty),
                      "current_price": str(ln.price)} for ln in lines],
        "action_type": "save_and_close",
    }
    if b.supplier_id:
        payload["store_load"]["supplier_id"] = b.supplier_id
    try:
        out = await run_in_threadpool(barsy.call, "Storeloads_create", payload)
    except Exception as e:
        b.status = "ready"                           # release the claim so it can be retried
        await session.commit()
        return {"ready": True, "sent": False, "error": str(e)[:160]}
    b.storeload_id = D._article_id(out)
    b.status = "finalised"
    b.finalised_at = _dt.datetime.now(_dt.timezone.utc)
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
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Upload a supplier delivery sheet → parse + match → persist a draft batch with its lines.
    NEW lines each get a linked intake draft so they surface on the Приемане page for enrichment."""
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)   # bounded read — don't buffer an oversized upload
    _check_size(raw)
    try:
        rows = await run_in_threadpool(_rows_from_upload, file.filename or "", raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Не мога да прочета файла: {str(e)[:140]}")
    parsed = await run_in_threadpool(_parse_rows, rows, supplier)   # first call loads the 22k-row map
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове за приемане.")

    source = "csv" if (file.filename or "").lower().endswith(".csv") else "excel"
    return await _persist_batch(session, actor, supplier, supplier_id, depot_id, reference,
                                doc_date, source, parsed)


async def _persist_batch(session, actor, supplier, supplier_id, depot_id, reference, doc_date,
                         source, parsed: list[dict]) -> dict:
    """Create a DeliveryBatch + its lines; each NEW line gets a linked intake draft so it surfaces
    on Приемане for enrichment. Shared by the file, edited-rows and OCR create paths."""
    b = DeliveryBatch(supplier=(supplier or "").strip(), supplier_id=supplier_id or None,
                      depot_id=depot_id or 1, reference=(reference or "").strip(),
                      doc_date=(doc_date or "").strip(), source=source, status="draft",
                      created_by=actor.email)
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
            nums = [D._to_float(c) for c in cells if any(ch.isdigit() for ch in c)]
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
    if ln.scanned_qty == 0:
        ln.status = "pending"
    elif ln.scanned_qty == ln.qty:
        ln.status = "confirmed"
    else:
        ln.status = "mismatch"       # over- or under-count


async def _guard_not_finalised(session: AsyncSession, batch_id: int) -> None:
    """Refuse mutations to a delivery that's already been sent to Barsy (local record must not drift
    from what Barsy recorded)."""
    b = await session.get(DeliveryBatch, batch_id)
    if b and b.status == "finalised":
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката е финализирана — не се променя.")


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
    if b.status == "finalised":
        raise HTTPException(status.HTTP_409_CONFLICT, "Доставката е финализирана.")
    code = (req.barcode or "").strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Празен баркод.")
    lines = await _lines(session, b.id)

    # 1) a line already carrying this barcode
    ln = next((x for x in lines if x.barcode and x.barcode == code), None)
    # 2) resolve the EAN → our code → the line matched to that code
    if ln is None:
        our = D._barcode_index()["ean_to_code"].get(code)
        if our:
            ln = next((x for x in lines if x.match_code and x.match_code == our), None)

    if ln is None:
        new_lines = [_line_out(x) for x in lines if x.is_new]
        return {"matched": False, "unknown": True, "barcode": code, "new_lines": new_lines}

    ln.scanned_qty += 1
    _set_line_status(ln)
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
    ln.scanned_qty += 1
    _set_line_status(ln)
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
    if b.status == "finalised":
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
    payload = {
        "store_load": {
            "depot_id": b.depot_id, "doc_date": b.doc_date or time.strftime("%Y-%m-%d"),
            "doc_num": doc_num, "operation_type": 1, "doc_type_id": 1,
        },
        "articles": [{"article_id": int(ln.article_id), "amount": str(ln.qty),
                      "current_price": str(ln.price)} for ln in lines],
        "action_type": "save_and_close",
    }
    if b.supplier_id:
        payload["store_load"]["supplier_id"] = b.supplier_id

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
    if claim.rowcount == 0:
        await session.refresh(b)
        if b.status == "finalised":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Вече е финализирана (storeload {b.storeload_id}).")
        raise HTTPException(status.HTTP_409_CONFLICT, "Финализирането вече тече.")

    # resolve supplier_id if we only have the name, then one storeload
    if b.supplier_id is None and b.supplier.strip():
        try:
            sid = await run_in_threadpool(D._resolve_supplier_id, b.supplier.strip())
            if sid:
                b.supplier_id = sid
                payload["store_load"]["supplier_id"] = sid
        except Exception:
            warnings.append("Доставчикът не бе намерен в Barsy — без supplier_id.")
    try:
        out = await run_in_threadpool(barsy.call, "Storeloads_create", payload)
    except Exception as e:
        b.status = "ready"            # release the claim so it can be retried
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy (доставка): {str(e)[:180]}")
    b.storeload_id = D._article_id(out)
    b.status = "finalised"
    b.finalised_at = _dt.datetime.now(_dt.timezone.utc)
    await session.commit()
    await log_action(session, actor, "delivery.finalise", target=b.supplier or b.reference,
                     detail=f"batch={b.id} storeload={b.storeload_id} lines={len(lines)}")
    return {"ok": True, "dry_run": False, "storeload_id": b.storeload_id,
            "storeload_payload": payload, "warnings": warnings}
