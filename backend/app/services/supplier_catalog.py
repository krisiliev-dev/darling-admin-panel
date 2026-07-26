# -*- coding: utf-8 -*-
"""Supplier catalogue — who supplies each product, at what code/price. Loaded once from
data/supplier_codes.jsonl. Extracted from routers/zayavki so catalog/pricing can use it without
reaching into a router's private helper (review: no service layer)."""
import os
from functools import lru_cache

from .jsonl import iter_jsonl

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "supplier_codes.jsonl")


@lru_cache(maxsize=1)
def supplier_catalog() -> dict:
    """Load supplier_codes.jsonl into:
      by_supplier[supplier] -> [ {code, ref, name, dprice} ... ]   (that supplier's catalogue)
      by_code[code]         -> { supplier -> {ref, name, dprice} } (who supplies a product + at what)
    """
    by_supplier: dict[str, list] = {}
    by_code: dict[str, dict] = {}
    for r in iter_jsonl(_DATA_PATH):             # per-line: one bad row can't truncate the rest
        sup = (r.get("supplier") or "").strip()
        code = str(r.get("code") or "").strip()
        if not sup or not code:
            continue
        row = {"code": code, "ref": str(r.get("ref") or ""),
               "name": r.get("rname") or "", "dprice": r.get("dprice") or 0.0}
        by_supplier.setdefault(sup, []).append(row)
        by_code.setdefault(code, {})[sup] = row
    return {"by_supplier": by_supplier, "by_code": by_code}


def suppliers_for(code: str) -> dict:
    return supplier_catalog()["by_code"].get(str(code), {})
