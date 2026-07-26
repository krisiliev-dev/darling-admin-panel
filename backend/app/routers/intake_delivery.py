# -*- coding: utf-8 -*-
"""Delivery-based intake (Приемане). A supplier hands over a delivery as an Excel sheet
(their code, product name, quantity, delivery price). The panel:

  1. /delivery/parse  — read the .xlsx, detect columns, and split rows into ALREADY-IN-CATALOG
     vs NEW (match seam: `_match_existing`). New rows are what staff must enrich.
  2. (per new product) reuse /intake/enrich for the AI draft; staff attach barcode + images.
  3. /delivery/commit — send the WHOLE delivery to Barsy as ONE storeload (доставка), so purchase
     analytics see a single, dated delivery instead of N ad-hoc article writes.

Two Barsy contracts are still being confirmed with the Hermes/Barsy owner and are therefore
isolated behind clearly-marked seams:
  * `_match_existing` — the real key is (supplier, supplier's own article code). Until the
    supplier-code mapping table lands, this falls back to a best-effort catalog match and flags
    low confidence. It is the ONLY place new-vs-existing is decided — swap it in one spot.
  * `_storeload_payload` / commit — the storeload create body shape. Guarded by the
    DELIVERY_BARSY_LIVE gate (default OFF => dry-run returns the payload, writes nothing).
"""
import re
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..barsy_ids import extract_id
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import User
from ..services.delivery_parse import _SUPPLIER_CODES_PATH  # noqa: F401  re-export (tests reference it)
from ..services.delivery_parse import detect_columns as _detect_columns
from ..services.delivery_parse import match_existing as _match_existing
from ..services.delivery_parse import read_rows as _read_rows
from ..services.delivery_parse import resolve_supplier_id as _resolve_supplier_id
from ..services.delivery_parse import to_float as _to_float
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/intake/delivery", tags=["intake"])

# ------------------------------------------------------------------ schemas ---
class DeliveryRow(BaseModel):
    row: int                       # 1-based source row (for staff to find it in their sheet)
    supplier_code: str = ""
    name: str = ""
    qty: float = 0.0
    delivery_price: float = 0.0
    is_new: bool = True
    match_code: str = ""
    match_name: str = ""
    match_method: str = ""         # 'code' | 'name' | ''
    match_confidence: str = ""     # 'high' | 'medium' | ''
    warnings: list[str] = []


class ParseResponse(BaseModel):
    columns: dict                  # {field: header-label used} — lets staff see what mapped
    positional: bool               # True => no header row detected, columns assumed by position
    rows: list[DeliveryRow]
    summary: dict                  # {total, new, existing, total_qty}


@router.post("/parse", response_model=ParseResponse)
async def parse_delivery(
    file: UploadFile = File(...),
    supplier: str = Form(""),
    user: User = Depends(current_active_user),
):
    """Read a supplier's delivery .xlsx and split rows into new vs already-in-catalog.
    Passing `supplier` sharpens the match (supplier + their code is the strongest key)."""
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Качи Excel файл (.xlsx) с доставката.")
    _cap = 15 * 1024 * 1024           # cap upload — guards memory / xlsx zip-bombs
    raw = await file.read(_cap + 1)   # bounded read — don't buffer an oversized upload
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файлът е празен.")
    if len(raw) > _cap:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файлът е твърде голям (макс. 15 MB).")
    try:
        rows = await run_in_threadpool(_read_rows, raw)
    except Exception as e:  # openpyxl raises many types on a bad file
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Не мога да прочета файла: {str(e)[:140]}")
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове в таблицата.")

    header = rows[0]
    cols = _detect_columns(header)
    positional = not cols
    if positional:
        cols = {"code": 0, "name": 1, "qty": 2, "price": 3}
        data_rows = rows                    # no header — every row is data
        col_labels = {k: f"колона {v + 1}" for k, v in cols.items()}
    else:
        data_rows = rows[1:]
        col_labels = {k: str(header[v]) for k, v in cols.items() if v < len(header)}

    out: list[DeliveryRow] = []
    for i, r in enumerate(data_rows):
        def cell(field, r=r):        # bind r per-iteration (closure is used within this iteration)
            j = cols.get(field)
            return r[j] if j is not None and j < len(r) else None
        nm = str(cell("name") or "").strip()
        code = str(cell("code") or "").strip()
        if not nm and not code:
            continue  # skip blank/spacer rows
        qty = _to_float(cell("qty"))
        price = _to_float(cell("price"))
        warns: list[str] = []
        if qty <= 0:
            warns.append("липсва количество")
        m = _match_existing(supplier, code, nm)
        # fall back to the mapping's known delivery price when the sheet doesn't carry one
        if price <= 0 and m and m.get("delivery_price"):
            price = _to_float(m["delivery_price"])
        out.append(DeliveryRow(
            row=i + (1 if positional else 2),
            supplier_code=code, name=nm, qty=qty, delivery_price=price,
            is_new=m is None,
            match_code=(m or {}).get("code", ""), match_name=(m or {}).get("name", ""),
            match_method=(m or {}).get("method", ""), match_confidence=(m or {}).get("confidence", ""),
            warnings=warns,
        ))
    new_n = sum(1 for r in out if r.is_new)
    return ParseResponse(
        columns=col_labels, positional=positional, rows=out,
        summary={"total": len(out), "new": new_n, "existing": len(out) - new_n,
                 "total_qty": round(sum(r.qty for r in out), 3)},
    )


# ------------------------------------------------------------------ commit → Barsy storeload ---
_UNIT_ID = {"бр": 1, "л": 2, "кг": 3, "ч": 4, "д": 5, "км": 6, "м": 7, "мин": 8,
            "м2": 11, "м3": 16}


class Attribute(BaseModel):
    key: str
    value: str


class CommitLine(BaseModel):
    supplier_code: str = ""
    name: str
    qty: float
    delivery_price: float = 0.0
    is_new: bool = True
    # existing product → its Barsy article id (resolved from the match)
    article_id: int | None = None
    # NEW product → the enriched, staff-approved fields (mirrors intake.ApproveRequest)
    code: str = ""             # OUR article code (business key)
    barcode: str = ""
    sale_price: str = ""       # € shelf price (staff/Microinvest — never AI, per price policy)
    unit: str = "бр"
    category: str = ""
    brand: str = ""            # → "Марка" article detail (Barsy has no native brand field)
    original_name: str = ""    # supplier's raw row name → article alias (POS/search), create-only
    oversell: bool = False     # orderable at <=0 stock (backorder / supplier dropship) → sale_below_zero
    weight_kg: str = ""        # kg → Barsy extra_data.weight_kg
    description: str = ""
    attributes: list[Attribute] = []


class CommitRequest(BaseModel):
    supplier: str = ""
    supplier_id: int | None = None  # Barsy supplier id; resolved from name at commit if absent
    reference: str = ""        # supplier's delivery/invoice number
    date: str = ""             # YYYY-MM-DD; empty => Barsy defaults to today
    confirm: bool = False
    lines: list[CommitLine]


def _new_article_body(ln: CommitLine) -> dict:
    """Articles_create body for a NEW product in the delivery (same contract as intake.push). This is
    a CREATE-only path (existing products are matched by id, never rebuilt here), so create-only fields
    (article_aliases) are safe to set — they'd 501 on re-send to an already-attached article."""
    art: dict = {
        "code": ln.code or ln.supplier_code,
        "article_name": ln.name,
        "article_name_ml": {"bg_BG": ln.name},
        "amount_type_id": _UNIT_ID.get(ln.unit, 1),
        "is_public": 1, "is_for_sale": 1,
    }
    if ln.oversell:
        art["sale_below_zero"] = "YES"                  # orderable at <=0 stock (backorder / dropship)
    if ln.supplier_code:
        art["ref_num"] = ln.supplier_code               # supplier catalog number — traceability (parity w/ barsy_push)
    alias = (ln.original_name or "").strip()
    if alias and alias != (ln.name or "").strip():
        art["article_aliases"] = [alias]                # supplier's raw name as a POS/search synonym
    if ln.weight_kg.strip():
        art["extra_data"] = {"weight_kg": ln.weight_kg.strip()}   # kg (Barsy extra_data object)
    if ln.description:
        art["description_ml"] = {"bg_BG": ln.description}
    if ln.barcode:
        art["barcode"] = ln.barcode
    if ln.sale_price:
        art["actual_price"] = str(ln.sale_price)
    details = [{"detail_name": a.key, "detail_value": a.value} for a in ln.attributes if a.key]
    # Brand has no native Barsy field — it's the "Марка" article detail (parity with intake.push /
    # barsy_push). Prepend it, unless the attributes already carry a Марка row.
    if ln.brand and not any(x["detail_name"] == "Марка" for x in details):
        details.insert(0, {"detail_name": "Марка", "detail_value": ln.brand.strip()})
    if details:
        art["details"] = details
    return {"article": art}


def _doc_num(req: CommitRequest) -> str:
    """Barsy requires a UNIQUE doc_num per trade object. Prefer the supplier's invoice/delivery
    reference; always suffix an epoch so a re-submit can't collide with an earlier attempt."""
    base = re.sub(r"\s+", "-", (req.reference or "DOST").strip())[:40]
    return f"{base}-{int(time.time())}"


def _storeload_payload(req: CommitRequest, line_article_ids: list[int | None]) -> dict:
    """Build the Barsy Storeloads_create body: one goods-in document with ALL articles in one
    `articles[]` array => a single dated delivery (verified shape, see barsy_stock.load_stock).
    `current_price` is the per-unit DELIVERY (cost) price so purchase COGS/analytics are accurate."""
    articles = []
    for ln, aid in zip(req.lines, line_article_ids):
        articles.append({
            "article_id": int(aid) if aid is not None else None,
            "amount": str(ln.qty),
            "current_price": str(ln.delivery_price),
        })
    cfg = get_settings()
    header = {
        "depot_id": cfg.barsy_depot_id,
        "doc_date": req.date or time.strftime("%Y-%m-%d"),
        "doc_num": _doc_num(req),
        "operation_type": 1,   # 1 = receipt (goods-in); part of the minimal validated shape
        "doc_type_id": 1,      # 1 = Фактура (Storeloads_getdocumenttypes)
        # prices are in EUR (barsy_currency_id=1) and VAT-INCLUSIVE (barsy_price_mode=1). Without these,
        # Barsy assumed the system default (ex-VAT), understating cost/margin by the VAT rate.
        "currency_id": cfg.barsy_currency_id,
        "price_mode": cfg.barsy_price_mode,
    }
    # supplier_id makes the delivery attributable in Barsy purchase analytics. We only hold the
    # supplier NAME here; resolve it to an id via Suppliers_getlist at commit time (live path).
    if req.supplier_id:
        header["supplier_id"] = req.supplier_id
    return {
        "store_load": header,
        "articles": articles,
        "action_type": "save_and_close",
    }


class CommitResponse(BaseModel):
    ok: bool
    dry_run: bool
    created_article_ids: list[int | None]
    storeload_payload: dict
    storeload_id: int | None = None
    warnings: list[str] = []


@router.post("/commit", response_model=CommitResponse)
async def commit_delivery(
    req: CommitRequest,
    actor: User = Depends(current_owner),   # ERP write — owner/manager only
    session=Depends(get_async_session),
):
    """Send the whole delivery to Barsy as ONE storeload. New products are created first, then a
    single storeload rows them all in. Guarded: needs confirm=true AND DELIVERY_BARSY_LIVE; until
    the gate is on this DRY-RUNS (validates, writes nothing, returns the exact payload)."""
    if not req.lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма редове за приемане.")
    warnings: list[str] = []
    for i, ln in enumerate(req.lines):
        if ln.qty <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Ред {i + 1} ({ln.name}) няма количество.")
        if ln.is_new and not (ln.code or ln.supplier_code):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Нов продукт „{ln.name}“ няма код — задай го преди приемане.")
        if not ln.is_new and ln.article_id is None:
            warnings.append(f"Ред {i + 1} „{ln.name}“ е маркиран съществуващ, но няма article_id.")

    live = get_settings().delivery_barsy_live and req.confirm

    # DRY-RUN: don't touch Barsy. Report the payload that WOULD be sent (new products get a
    # placeholder id so staff can see the row mapping).
    if not live:
        placeholder_ids: list[int | None] = [None if ln.is_new else ln.article_id for ln in req.lines]
        payload = _storeload_payload(req, placeholder_ids)
        if not get_settings().delivery_barsy_live:
            warnings.append("DELIVERY_BARSY_LIVE е изключен — това е проба (нищо не се записва).")
        elif not req.confirm:
            warnings.append("Потвърди с confirm=true, за да се запише в Barsy.")
        await log_action(session, actor, "intake.delivery.dry_run",
                         target=req.supplier or req.reference,
                         detail=f"lines={len(req.lines)} new={sum(1 for l in req.lines if l.is_new)}")
        return CommitResponse(ok=True, dry_run=True, created_article_ids=placeholder_ids,
                              storeload_payload=payload, warnings=warnings)

    # LIVE. Resolve the supplier name → Barsy supplier_id (attributes the delivery for purchase
    # analytics); non-fatal if it can't be found.
    if req.supplier_id is None and req.supplier.strip():
        try:
            req.supplier_id = await run_in_threadpool(_resolve_supplier_id, req.supplier.strip())
        except Exception:
            warnings.append("Доставчикът не бе намерен в Barsy — доставката се записва без supplier_id.")

    # create new articles, then one storeload. NOTE: this legacy stateless path can't dedup a
    # partial retry (nothing is persisted per-line). If a create fails mid-way, we surface exactly
    # which articles already landed in Barsy so staff don't blind-retry into duplicates. The stateful
    # Доставки flow (deliveries.py) is the preferred path — it persists article_id per line.
    created: list[int | None] = []
    for ln in req.lines:
        if not ln.is_new:
            created.append(ln.article_id)
            continue
        try:
            new_body = _new_article_body(ln)
            new_body["article"]["min_profit_percent"] = get_settings().barsy_min_profit_percent  # POS below-cost floor
            out = await run_in_threadpool(barsy.call, "Articles_create", new_body)
        except Exception as e:
            done = [str(c) for c in created if c]
            hint = (f" Вече създадени в Barsy: {', '.join(done)} — провери преди повторение."
                    if done else "")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"Barsy (създаване на „{ln.name}“): {str(e)[:140]}.{hint}")
        created.append(_article_id(out))

    payload = _storeload_payload(req, created)
    try:
        sl = await run_in_threadpool(barsy.call, "Storeloads_create", payload)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Barsy (доставка): {str(e)[:180]}. Артикулите са създадени — "
                            "провери в Barsy преди повторно приемане.")
    await log_action(session, actor, "intake.delivery.commit",
                     target=req.supplier or req.reference,
                     detail=f"lines={len(req.lines)} storeload={_article_id(sl)}")
    return CommitResponse(ok=True, dry_run=False, created_article_ids=created,
                          storeload_payload=payload, storeload_id=_article_id(sl), warnings=warnings)


def _article_id(out):
    """Barsy create may return a bare int or a dict with id/article_id/storeload_id. Shared parser."""
    return extract_id(out, ("article_id", "storeload_id", "id"))
