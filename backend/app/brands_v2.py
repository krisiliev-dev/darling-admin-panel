# -*- coding: utf-8 -*-
"""Simplified brand registry (v2) read layer. ONE flat source of truth:
brand_registry_v2.json — brand -> {canonical, aliases, prefixes, series, gs1_owner, logo}.
Resolution is two-path (prefix OR alias-in-name); product counts come from the resolver's
confirmed_brands.json. Read-only here; editing/persistence is a separate decision."""
import json
import pathlib
from collections import Counter

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "brands"
_REG = _DATA / "brand_registry_v2.json"
_CONF = _DATA / "confirmed_brands.json"

SCHEMA_VERSION = 2


def _load_reg() -> dict:
    return json.loads(_REG.read_text(encoding="utf-8")) if _REG.exists() else {"brands": {}}


def _counts() -> tuple[Counter, Counter]:
    """(products_per_brand, by_tier) from confirmed_brands.json — {} when absent."""
    prod: Counter = Counter()
    tier: Counter = Counter()
    if _CONF.exists():
        for c in json.loads(_CONF.read_text(encoding="utf-8")).values():
            prod[c["brand"]] += 1
            tier[c["tier"]] += 1
    return prod, tier


def summary() -> dict:
    reg = _load_reg()
    brands = reg.get("brands", {})
    prod, tier = _counts()
    with_pref = sum(1 for b in brands.values() if b.get("prefixes"))
    return {
        "schema_version": reg.get("schema_version", SCHEMA_VERSION),
        "brands": len(brands),
        "brands_with_prefixes": with_pref,
        "total_prefixes": sum(len(b.get("prefixes") or []) for b in brands.values()),
        "total_series": sum(len(b.get("series") or []) for b in brands.values()),
        "branded_products": sum(prod.values()),
        "by_tier": dict(tier),
    }


def brands() -> list[dict]:
    reg = _load_reg()
    prod, _ = _counts()
    rows = []
    for bid, b in reg.get("brands", {}).items():
        rows.append({
            "id": bid,
            "canonical": b["canonical"],
            "aliases": b.get("aliases") or [],
            "prefixes": b.get("prefixes") or [],
            "series": b.get("series") or [],
            "gs1_owner": b.get("gs1_owner"),
            "logo": b.get("logo"),
            "product_count": prod.get(b["canonical"], 0),
        })
    # most products first, then alphabetical
    rows.sort(key=lambda r: (-r["product_count"], r["canonical"].lower()))
    return rows


def brand(bid: str) -> dict | None:
    for r in brands():
        if r["id"] == bid:
            return r
    return None
