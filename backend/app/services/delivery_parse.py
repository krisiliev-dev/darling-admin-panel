# -*- coding: utf-8 -*-
"""Delivery-sheet parsing + new-vs-existing matching, shared by the Приемане (intake_delivery) and
Доставки (deliveries) flows. Extracted from routers/intake_delivery so deliveries stops importing a
router's private helpers (review: no service layer). Pure/data-driven except resolve_supplier_id,
which does a single Barsy lookup on the live commit path.
"""
import io
import os
from functools import lru_cache

from ..core import barsy
from .jsonl import iter_jsonl
from .product_seed import products
from .text import norm

_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# ------------------------------------------------------------------ column detection ---
# Supplier sheets are hand-made and inconsistent. Detect each column by header keyword;
# fall back to positional order [code, name, qty, price] when no header is recognised.
HEADERS = {
    "code": ("код", "code", "каталог", "catalog", "sku", "арт", "номер", "no", "№"),
    "name": ("наименование", "име", "name", "продукт", "стока", "описание", "артикул"),
    "qty": ("количество", "к-во", "кол", "брой", "бр", "qty", "quantity", "count"),
    "price": ("цена", "price", "доставна", "ед.цена", "ед. цена", "стойност", "ст-ст", "cost"),
    "barcode": ("баркод", "barcode", "ean", "щрихкод", "штрих", "gtin"),
    # deliberately NOT "%" — far too common a header to guess a discount from
    "discount": ("отстъпка", "discount", "намаление", "рабат", "търг. отст."),
}


def detect_columns(header: list) -> dict:
    """Map each logical field to a 0-based column index using header keywords. 'name' is required
    when a header row exists; the rest are best-effort. Returns {} to signal 'no header — positional'."""
    cells = [norm(c) for c in header]
    found: dict[str, int] = {}
    for field, keys in HEADERS.items():
        for idx, cell in enumerate(cells):
            if not cell or idx in found.values():
                continue
            if any(cell == norm(k) or cell.startswith(norm(k)) for k in keys):
                found[field] = idx
                break
    return found if "name" in found else {}


def to_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    import re
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else 0.0


# ------------------------------------------------------------------ new-vs-existing ---
# The authoritative match key is (supplier, the supplier's own article code). It lives in
# supplier_codes.jsonl — {code(=OUR code), supplier, ref(=their code), rname, dprice}.
_SUPPLIER_CODES_PATH = os.path.join(_DATA, "supplier_codes.jsonl")
_BARCODES_PATH = os.path.join(_DATA, "barcodes.jsonl")


def _stamp(path: str) -> float:
    """The file's mtime, used as a CACHE KEY. A plain lru_cache over a file read pins the
    first version for the life of the process: regenerating the snapshot changed nothing
    until a restart, which is half of why a product we created stayed invisible."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def refresh_catalog_caches() -> None:
    """Drop the cached snapshots. Call after creating an article in Barsy so the very next
    lookup re-reads whatever is on disk instead of a snapshot taken before it existed."""
    _supplier_index_at.cache_clear()
    _barcode_index_at.cache_clear()
    _seed_by_code_at.cache_clear()


def supplier_index() -> dict:
    return _supplier_index_at(_stamp(_SUPPLIER_CODES_PATH))


@lru_cache(maxsize=2)
def _supplier_index_at(_mtime: float) -> dict:
    """Load the supplier-code mapping into fast lookup tables. Keys are normalised."""
    by_sup_ref: dict[tuple, dict] = {}   # (supplier, their code) -> row   [strongest]
    by_ref: dict[str, dict] = {}         # their code alone        -> row   [medium]
    by_sup_name: dict[tuple, dict] = {}  # (supplier, their name)  -> row   [medium]
    by_name: dict[str, dict] = {}        # their name alone        -> row   [low]
    for r in iter_jsonl(_SUPPLIER_CODES_PATH):
        sup, ref, rname = norm(r.get("supplier")), norm(r.get("ref")), norm(r.get("rname"))
        if ref:
            by_sup_ref.setdefault((sup, ref), r)
            by_ref.setdefault(ref, r)
        if rname:
            by_sup_name.setdefault((sup, rname), r)
            by_name.setdefault(rname, r)
    return {"by_sup_ref": by_sup_ref, "by_ref": by_ref,
            "by_sup_name": by_sup_name, "by_name": by_name}


def barcode_index() -> dict:
    return _barcode_index_at(_stamp(_BARCODES_PATH))


@lru_cache(maxsize=2)
def _barcode_index_at(_mtime: float) -> dict:
    """Two maps from the catalogue's known EANs: ean→our code, and our code→[eans]."""
    ean_to_code: dict[str, str] = {}
    code_to_eans: dict[str, list] = {}
    for r in iter_jsonl(_BARCODES_PATH):
        code, ean = str(r.get("code", "")).strip(), str(r.get("ean", "")).strip()
        if code and ean:
            ean_to_code.setdefault(ean, code)
            code_to_eans.setdefault(code, []).append(ean)
    return {"ean_to_code": ean_to_code, "code_to_eans": code_to_eans}


def seed_by_code() -> dict:
    return _seed_by_code_at(0.0)


@lru_cache(maxsize=2)
def _seed_by_code_at(_mtime: float) -> dict:
    """Our own catalogue keyed by our code — a secondary net when a sheet already uses our codes."""
    out: dict[str, dict] = {}
    for p in products():
        code = str(p.get("code") or "").strip()
        if code:
            out.setdefault(code, p)
    return out


def match_existing(supplier: str, supplier_code: str, name: str) -> dict | None:
    """THE new-vs-existing decision (single seam). Returns a match dict (with our `code`) or None."""
    idx = supplier_index()
    sup, ref, nm = norm(supplier), norm(supplier_code), norm(name)

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
    if code and code in seed_by_code():
        p = seed_by_code()[code]
        return {"code": p["code"], "name": p["name"], "method": "code", "confidence": "high",
                "delivery_price": None}
    return None


def read_rows(raw: bytes) -> list[list]:
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


def resolve_supplier_id(name: str) -> int | None:
    """Map a supplier NAME to its Barsy supplier_id (Suppliers_getlist). Exact normalised match;
    None if not found. Called only on the live commit path."""
    rows = barsy.call("Suppliers_getlist", {"extra_properties": ["min"]}) or []
    target = norm(name)
    for r in rows:
        if r.get("delete_flag"):
            continue
        if norm(r.get("supplier_name")) == target:
            sid = r.get("supplier_id")
            return int(sid) if sid is not None else None
    return None


# ------------------------------------------------------------------ blank rows ---
def is_blank_row(r: list) -> bool:
    return all(c is None or str(c).strip() == "" for c in r)


def strip_leading_blank(rows: list[list]) -> list[list]:
    """Drop fully-empty rows at the TOP. A supplier who leaves row 1 blank and puts the header
    on row 2 used to make auto-detect read that header as a product; skipping leading blanks
    fixes it for the no-profile path too (the owner hit exactly this)."""
    i = 0
    while i < len(rows) and is_blank_row(rows[i]):
        i += 1
    return rows[i:]
