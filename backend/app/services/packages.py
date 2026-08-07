# -*- coding: utf-8 -*-
"""Разфасовки — Barsy's pack nomenclature, mirrored read-only.

Barsy keeps named packs with a numeric Количество (Каса 12, Кашон 8, Стек 6, Стек 24) under
Настройки → Артикули → Разфасовки, and its own hint says they exist to make ЗАРЕЖДАНЕ easier with a
default settable per article. We read theirs rather than keep a second list that would drift out of
agreement with it the first time somebody adds a pack size in Barsy.

Cached in-process because it is a tiny, rarely-changing list and the low-tier Barsy server is slow;
`refresh()` drops it, and the router exposes that so nobody has to restart a pod to see a new pack.
"""
import threading

from ..core import barsy

# (packs, fetched_ok). Guarded by a lock: two requests arriving together should make ONE Barsy call,
# not two — this list is read on every delivery page load.
_cache: list[dict] | None = None
_lock = threading.Lock()


def _num(v) -> float:
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _normalise(rows) -> list[dict]:
    """Barsy rows → {id, name, qty}. Only packs with a POSITIVE quantity are usable as a factor;
    a zero would silently turn a delivery into nothing."""
    out: list[dict] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        raw_id = r.get("package_id") or r.get("id")
        if raw_id is None:
            continue
        qty = _num(r.get("amount") or r.get("quantity") or r.get("qty") or r.get("count"))
        name = str(r.get("name") or r.get("title") or "").strip()
        try:
            pid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            out.append({"id": pid, "name": name, "qty": qty})
    out.sort(key=lambda x: (x["name"], x["qty"]))
    return out


def fetch(force: bool = False) -> list[dict]:
    """The pack list. Never raises — a delivery must stay usable when Barsy is unreachable; the
    operator simply gets no pack suggestions rather than a broken page."""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        try:
            out = barsy.call("Packages_index", {"action_type": "values", "filters": []},
                             is_read=True)
        except Exception:
            return _cache or []
        rows = out if isinstance(out, list) else (
            out.get("data") or out.get("values") or out.get("packages") or [])
        _cache = _normalise(rows)
        return _cache


def refresh() -> list[dict]:
    return fetch(force=True)


def by_id(pack_id: int) -> dict | None:
    """A pack by its Barsy id. ALWAYS by id — the names are not unique: „Стек" is both 6 and 24, so
    matching on a name would pick one of them arbitrarily and multiply a delivery by the wrong
    number."""
    for p in fetch():
        if p["id"] == pack_id:
            return p
    return None
