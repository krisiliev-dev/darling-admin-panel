# -*- coding: utf-8 -*-
"""Simplified brand registry (v2) — the SINGLE editable source of truth (owner 2026-08-02).

SEED vs LIVE: the pipeline ships `brand_registry_v2.seed.json` (tracked). On first use the
app copies it to `brand_registry_v2.json` (the LIVE file, git-ignored) and every edit writes
the LIVE file. A deploy therefore never clobbers owner edits, and the live JSON really is the
one source. Reads/edits are serialized with a lock; writes are atomic (temp + replace).

Resolution is two-path (prefix OR alias-in-name); product counts come from confirmed_brands.json.
"""
import json
import os
import pathlib
import shutil
import tempfile
import threading
from collections import Counter

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "brands"
_SEED = _DATA / "brand_registry_v2.seed.json"
_LIVE = pathlib.Path(os.environ.get("BRAND_REGISTRY_PATH") or (_DATA / "brand_registry_v2.json"))
_CONF = _DATA / "confirmed_brands.json"
_LOCK = threading.Lock()
SCHEMA_VERSION = 2


def _ensure_live() -> None:
    if not _LIVE.exists():
        _LIVE.parent.mkdir(parents=True, exist_ok=True)
        if _SEED.exists():
            shutil.copy2(_SEED, _LIVE)
        else:
            _LIVE.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "brands": {}},
                                        ensure_ascii=False), encoding="utf-8")


def _load() -> dict:
    _ensure_live()
    return json.loads(_LIVE.read_text(encoding="utf-8"))


def _save(reg: dict) -> None:
    _LIVE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(_LIVE.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, _LIVE)


def _counts():
    prod: Counter = Counter()
    tier: Counter = Counter()
    if _CONF.exists():
        for c in json.loads(_CONF.read_text(encoding="utf-8")).values():
            prod[c["brand"]] += 1
            tier[c["tier"]] += 1
    return prod, tier


def summary() -> dict:
    reg = _load()
    brands = reg.get("brands", {})
    prod, tier = _counts()
    return {
        "schema_version": reg.get("schema_version", SCHEMA_VERSION),
        "brands": len(brands),
        "brands_with_prefixes": sum(1 for b in brands.values() if b.get("prefixes")),
        "total_prefixes": sum(len(b.get("prefixes") or []) for b in brands.values()),
        "total_series": sum(len(b.get("series") or []) for b in brands.values()),
        "branded_products": sum(prod.values()),
        "by_tier": dict(tier),
    }


def _row(bid, b, prod):
    return {"id": bid, "canonical": b["canonical"], "aliases": b.get("aliases") or [],
            "prefixes": b.get("prefixes") or [], "series": b.get("series") or [],
            "gs1_owner": b.get("gs1_owner"), "logo": b.get("logo"),
            "product_count": prod.get(b["canonical"], 0)}


def brands() -> list[dict]:
    reg = _load()
    prod, _ = _counts()
    rows = [_row(bid, b, prod) for bid, b in reg.get("brands", {}).items()]
    rows.sort(key=lambda r: (-r["product_count"], r["canonical"].lower()))
    return rows


def brand(bid: str) -> dict | None:
    reg = _load()
    b = reg.get("brands", {}).get(bid)
    if not b:
        return None
    prod, _ = _counts()
    return _row(bid, b, prod)


# ---- edits (all serialized + atomic). Return the updated row or raise KeyError/ValueError.
_FIELDS = {"aliases", "prefixes", "series"}


def edit(bid: str, *, canonical=None, gs1_owner=None, logo=None,
         add=None, remove=None) -> dict:
    """Patch a brand. `add`/`remove` are {field: [values]} for aliases/prefixes/series.
    Prefixes are digit-only 9-char; aliases are lowercased. canonical renames the display."""
    with _LOCK:
        reg = _load()
        b = reg.get("brands", {}).get(bid)
        if not b:
            raise KeyError(bid)
        if canonical is not None:
            c = str(canonical).strip()
            if not c:
                raise ValueError("canonical cannot be empty")
            b["canonical"] = c
        if gs1_owner is not None:
            b["gs1_owner"] = str(gs1_owner).strip() or None
        if logo is not None:
            b["logo"] = str(logo).strip() or None
        for field, vals in (add or {}).items():
            if field not in _FIELDS:
                raise ValueError(f"cannot add to {field}")
            cur = set(b.get(field) or [])
            for v in vals:
                v = str(v).strip()
                if field == "aliases":
                    v = v.lower()
                    if not v or v == b["canonical"].lower():
                        continue
                elif field == "prefixes":
                    v = "".join(ch for ch in v if ch.isdigit())
                    if len(v) < 9:
                        raise ValueError(f"prefix must be >=9 digits: {v!r}")
                    v = v[:9]
                if v:
                    cur.add(v)
            b[field] = sorted(cur)
        for field, vals in (remove or {}).items():
            if field not in _FIELDS:
                raise ValueError(f"cannot remove from {field}")
            norm = {(str(v).strip().lower() if field == "aliases" else str(v).strip())
                    for v in vals}
            b[field] = [x for x in (b.get(field) or [])
                        if (x.lower() if field == "aliases" else x) not in norm]
        _save(reg)
        prod, _ = _counts()
        return _row(bid, b, prod)


def create(bid: str, canonical: str) -> dict:
    with _LOCK:
        reg = _load()
        reg.setdefault("brands", {})
        if bid in reg["brands"]:
            raise ValueError("brand already exists")
        reg["brands"][bid] = {"canonical": str(canonical).strip() or bid,
                              "aliases": [], "prefixes": [], "series": [],
                              "gs1_owner": None, "logo": None}
        _save(reg)
        return _row(bid, reg["brands"][bid], Counter())


def delete(bid: str) -> None:
    with _LOCK:
        reg = _load()
        if bid not in reg.get("brands", {}):
            raise KeyError(bid)
        del reg["brands"][bid]
        _save(reg)
