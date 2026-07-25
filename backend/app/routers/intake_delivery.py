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
import io
import json
import os
import re
import time
import unicodedata
from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import User
from ..users import current_active_user, current_owner
from .inventory_health import _products  # reuse the cached seed catalogue (interim match source)

router = APIRouter(prefix="/api/intake/delivery", tags=["intake"])

# ------------------------------------------------------------------ column detection ---
# Supplier sheets are hand-made and inconsistent. Detect each column by header keyword;
# fall back to positional order [code, name, qty, price] when no header is recognised.
_HEADERS = {
    "code": ("код", "code", "каталог", "catalog", "sku", "арт", "номер", "no", "№"),
    "name": ("наименование", "име", "name", "продукт", "стока", "описание", "артикул"),
    "qty": ("количество", "к-во", "кол", "брой", "бр", "qty", "quantity", "count"),
    "price": ("цена", "price", "доставна", "ед.цена", "ед. цена", "стойност", "ст-ст", "cost"),
}


def _norm(s: str) -> str:
    """Lowercase, strip accents/spaces/punct — for fuzzy header + name comparison."""
    s = unicodedata.normalize("NFKD", str(s or "")).lower()
    return re.sub(r"[\s\.\-_/№#]+", "", s)


def _detect_columns(header: list) -> dict:
    """Map each logical field to a 0-based column index using header keywords. 'name' is required
    when a header row exists; the rest are best-effort. Returns {} to signal 'no header — positional'."""
    cells = [_norm(c) for c in header]
    found: dict[str, int] = {}
    for field, keys in _HEADERS.items():
        for idx, cell in enumerate(cells):
            if not cell or idx in found.values():
                continue
            if any(cell == _norm(k) or cell.startswith(_norm(k)) for k in keys):
                found[field] = idx
                break
    return found if "name" in found else {}


def _to_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else 0.0


# ------------------------------------------------------------------ new-vs-existing ---
# The authoritative match key is (supplier, the supplier's own article code). It lives in
# supplier_codes.jsonl — 22,807 rows of {code(=OUR code), supplier, ref(=their code),
# rname(=their name for it), dprice} sourced from Barsy's suppliers_details / the ERP export.
_SUPPLIER_CODES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "data", "supplier_codes.jsonl")


@lru_cache(maxsize=1)
def _supplier_index() -> dict:
    """Load the supplier-code mapping into fast lookup tables. Keys are normalised.
    Falls back to an empty index if the file is absent (then only the seed-catalogue nets apply)."""
    by_sup_ref: dict[tuple, dict] = {}   # (supplier, their code) -> row   [strongest]
    by_ref: dict[str, dict] = {}         # their code alone        -> row   [medium]
    by_sup_name: dict[tuple, dict] = {}  # (supplier, their name)  -> row   [medium]
    by_name: dict[str, dict] = {}        # their name alone        -> row   [low]
    try:
        with open(_SUPPLIER_CODES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                sup, ref, rname = _norm(r.get("supplier")), _norm(r.get("ref")), _norm(r.get("rname"))
                if ref:
                    by_sup_ref.setdefault((sup, ref), r)
                    by_ref.setdefault(ref, r)
                if rname:
                    by_sup_name.setdefault((sup, rname), r)
                    by_name.setdefault(rname, r)
    except (OSError, json.JSONDecodeError):
        pass
    return {"by_sup_ref": by_sup_ref, "by_ref": by_ref,
            "by_sup_name": by_sup_name, "by_name": by_name}


_BARCODES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "barcodes.jsonl")


@lru_cache(maxsize=1)
def _barcode_index() -> dict:
    """Two maps from the catalogue's known EANs: ean→our code, and our code→[eans]. Lets the
    delivery scanner resolve a scanned barcode to a product even when the sheet had no barcode."""
    ean_to_code: dict[str, str] = {}
    code_to_eans: dict[str, list] = {}
    try:
        with open(_BARCODES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                code, ean = str(r.get("code", "")).strip(), str(r.get("ean", "")).strip()
                if code and ean:
                    ean_to_code.setdefault(ean, code)
                    code_to_eans.setdefault(code, []).append(ean)
    except (OSError, json.JSONDecodeError):
        pass
    return {"ean_to_code": ean_to_code, "code_to_eans": code_to_eans}


@lru_cache(maxsize=1)
def _seed_by_code() -> dict:
    """Our own catalogue keyed by our code — a secondary net when a sheet already uses our codes."""
    out: dict[str, dict] = {}
    for p in _products():
        code = str(p.get("code") or "").strip()
        if code:
            out.setdefault(code, p)
    return out


def _match_existing(supplier: str, supplier_code: str, name: str) -> dict | None:
    """THE new-vs-existing decision (single seam). Returns a match dict (with our `code`) or None.

    Priority, strongest first:
      1. (supplier, their code)      → high
      2. their code, any supplier    → medium   (codes can collide across suppliers)
      3. (supplier, their name)      → high
      4. their name, any supplier    → low
      5. their code == one of OUR codes (sheet already uses our codes) → high
    `method`/`confidence` are surfaced so staff see *why* a row was called existing and can override.
    """
    idx = _supplier_index()
    sup, ref, nm = _norm(supplier), _norm(supplier_code), _norm(name)

    def _hit(row, method, conf):
        return {"code": str(row.get("code", "")), "name": row.get("rname") or name,
                "method": method, "confidence": conf,
                "delivery_price": row.get("dprice")}

    if sup and ref and (sup, ref) in idx["by_sup_ref"]:
        return _hit(idx["by_sup_ref"][(sup, ref)], "code", "high")
    if ref and ref in idx["by_ref"]:
        return _hit(idx["by_ref"][ref], "code", "medium")
    if sup and nm and (sup, nm) in idx["by_sup_name"]:
        return _hit(idx["by_sup_name"][(sup, nm)], "name", "high")
    if nm and nm in idx["by_name"]:
        return _hit(idx["by_name"][nm], "name", "low")
    code = (supplier_code or "").strip()
    if code and code in _seed_by_code():
        p = _seed_by_code()[code]
        return {"code": p["code"], "name": p["name"], "method": "code", "confidence": "high",
                "delivery_price": None}
    return None


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
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файлът е празен.")
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
        def cell(field):
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


def _read_rows(raw: bytes) -> list[list]:
    """Load the first worksheet as a list of value-rows (values-only, fast)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    # drop fully-empty trailing rows
    while rows and all(c is None or str(c).strip() == "" for c in rows[-1]):
        rows.pop()
    return rows


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
    """Articles_create body for a NEW product in the delivery (same contract as intake.push)."""
    art: dict = {
        "code": ln.code or ln.supplier_code,
        "article_name": ln.name,
        "article_name_ml": {"bg_BG": ln.name},
        "amount_type_id": _UNIT_ID.get(ln.unit, 1),
        "is_public": 1, "is_for_sale": 1,
    }
    if ln.description:
        art["description_ml"] = {"bg_BG": ln.description}
    if ln.barcode:
        art["barcode"] = ln.barcode
    if ln.sale_price:
        art["actual_price"] = str(ln.sale_price)
    if ln.attributes:
        art["details"] = [{"detail_name": a.key, "detail_value": a.value} for a in ln.attributes if a.key]
    return {"article": art}


def _resolve_supplier_id(name: str) -> int | None:
    """Map a supplier NAME to its Barsy supplier_id (Suppliers_getlist). Exact normalised match;
    None if not found. Called only on the live commit path."""
    rows = barsy.call("Suppliers_getlist", {"extra_properties": ["min"]}) or []
    target = _norm(name)
    for r in rows:
        if r.get("delete_flag"):
            continue
        if _norm(r.get("supplier_name")) == target:
            sid = r.get("supplier_id")
            return int(sid) if sid is not None else None
    return None


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
    header = {
        "depot_id": get_settings().barsy_depot_id,
        "doc_date": req.date or time.strftime("%Y-%m-%d"),
        "doc_num": _doc_num(req),
        "operation_type": 1,   # 1 = receipt (goods-in); part of the minimal validated shape
        "doc_type_id": 1,      # 1 = Фактура (Storeloads_getdocumenttypes)
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

    # create new articles, then one storeload.
    created: list[int | None] = []
    for ln in req.lines:
        if not ln.is_new:
            created.append(ln.article_id)
            continue
        try:
            out = await run_in_threadpool(barsy.call, "Articles_create", _new_article_body(ln))
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"Barsy (създаване на „{ln.name}“): {str(e)[:160]}")
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
    """Barsy create may return a bare int or a dict with id/article_id/storeload_id."""
    if isinstance(out, int):
        return out
    if isinstance(out, str) and out.strip().isdigit():
        return int(out.strip())
    if isinstance(out, dict):
        for k in ("article_id", "storeload_id", "id"):
            v = out.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
    return None
