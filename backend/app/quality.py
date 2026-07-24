# -*- coding: utf-8 -*-
"""Product-data quality checks (owner 2026-07-21). The owner audits manually; this is a SECOND
automated safety net — over the whole Barsy catalog and on a single product being added. Go-live is
a one-way door for data quality (duplicates + gaps are cheap to catch now, expensive on live
listings), so the checks are conservative and explain themselves.

Pure functions here (testable); the HTTP surface is routers/quality.py."""
import re

# Darling's own internally-assigned prefixes are NOT real GTINs (see barcode-ean memory). Flag so the
# connector can push identifier_exists=false to Shopify instead of a fake barcode (Google rejects those).
_INTERNAL_PREFIXES = ("380013",)


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def first_barcode(v):
    """Barsy barcode may be a string or a list; take the first non-empty."""
    if isinstance(v, list):
        return next((str(x) for x in v if x), "")
    return str(v or "")


def ean_valid(code: str) -> bool:
    """EAN-8/EAN-13 check-digit validation."""
    d = digits(code)
    if len(d) not in (8, 13):
        return False
    body, check = d[:-1], int(d[-1])
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def is_internal_barcode(code: str) -> bool:
    d = digits(code)
    return any(d.startswith(p) for p in _INTERNAL_PREFIXES)


def norm_name(name: str) -> str:
    """Normalized key for near-duplicate detection: lowercase, collapse whitespace/punctuation."""
    return re.sub(r"[^\wа-я0-9]+", " ", str(name or "").lower(), flags=re.UNICODE).strip()


def check_article(a: dict) -> list[str]:
    """Per-product completeness/validity issues (Bulgarian messages). Empty list = clean."""
    issues: list[str] = []
    if not str(a.get("article_name") or a.get("name") or "").strip():
        issues.append("няма име")

    bc = first_barcode(a.get("barcode"))
    if not bc.strip():
        issues.append("няма баркод")
    elif is_internal_barcode(bc):
        issues.append("вътрешен код (не е реален GTIN)")
    elif not ean_valid(bc):
        issues.append("невалиден баркод (контролна цифра)")

    price = a.get("actual_price")
    if price is None:
        price = a.get("price")
    try:
        if price is None or float(price) <= 0:
            issues.append("няма продажна цена")
    except (TypeError, ValueError):
        issues.append("невалидна цена")

    cat = a.get("master_cat") or a.get("categories_names") or a.get("category")
    if not str(cat or "").strip():
        issues.append("няма категория")

    desc = str(a.get("description") or "").strip()
    if len(desc) < 10:
        issues.append("кратко/липсващо описание")

    if not str(a.get("picture") or a.get("image") or "").strip():
        issues.append("няма снимка")
    return issues


def audit_catalog(rows: list[dict]) -> dict:
    """Whole-catalog audit: per-article issues + cross-article duplicate detection (barcode, code,
    near-duplicate name). Returns a summary + a flagged worklist."""
    by_barcode: dict[str, list] = {}
    by_code: dict[str, list] = {}
    by_name: dict[str, list] = {}
    flagged = []
    for a in rows or []:
        aid = a.get("article_id")
        name = a.get("article_name") or a.get("name") or ""
        bc = digits(first_barcode(a.get("barcode")))
        code = str(a.get("code") or "").strip()
        if bc and not is_internal_barcode(bc):
            by_barcode.setdefault(bc, []).append(aid)
        if code:
            by_code.setdefault(code, []).append(aid)
        nn = norm_name(name)
        if nn:
            by_name.setdefault(nn, []).append(aid)

    dup_barcode = {k: v for k, v in by_barcode.items() if len(v) > 1}
    dup_code = {k: v for k, v in by_code.items() if len(v) > 1}
    dup_name = {k: v for k, v in by_name.items() if len(v) > 1}
    dup_barcode_ids = {i for v in dup_barcode.values() for i in v}
    dup_code_ids = {i for v in dup_code.values() for i in v}
    dup_name_ids = {i for v in dup_name.values() for i in v}

    for a in rows or []:
        aid = a.get("article_id")
        issues = check_article(a)
        if aid in dup_barcode_ids:
            issues.append("дублиран баркод")
        if aid in dup_code_ids:
            issues.append("дублиран код")
        if aid in dup_name_ids:
            issues.append("възможен дубликат (име)")
        if issues:
            flagged.append({
                "article_id": aid, "code": a.get("code") or "",
                "name": a.get("article_name") or a.get("name") or "", "issues": issues,
            })

    total = len(rows or [])
    return {
        "total": total,
        "flagged_count": len(flagged),
        "clean_count": total - len(flagged),
        "duplicate_barcodes": len(dup_barcode),
        "duplicate_codes": len(dup_code),
        "duplicate_names": len(dup_name),
        "flagged": flagged,
    }
