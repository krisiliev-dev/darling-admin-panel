# -*- coding: utf-8 -*-
"""Brand registry — read layer over the pipeline's snapshot, with the panel's human overrides on top.

TWO KINDS OF FACT, DELIBERATELY IN TWO PLACES (architecture agreed with Hermes 2026-07-31):

  • `brand_registry.json` is MACHINE-DERIVED — GS1 imports, research agents, resolution rules. It is
    regenerated freely by the pipeline and is READ-ONLY here. It ships in this repo so the deploy
    carries it (the alternative, reading it from a path on the box, is exactly the #98 hole).
  • Human corrections live in the panel's own `brand_override` table, and WIN.

Keeping them separate is what stops a regeneration silently discarding somebody's judgement — the
failure mode already seen with `gazetteer.jsonl`, where hand-edits were overwritten on the next build.
It also makes every correction attributable: one `asserts` flip changes what thousands of products are
called, and "who decided this, and when" should be answerable.

⚠ `asserts` IS THE ONLY GATE. A prefix may carry a `brand_id` WITHOUT asserting — that is a suggestion
awaiting review (465 of them), not something to print on a product. Flattening the two would auto-label
products from unreviewed guesses.
"""
import json
import logging
import pathlib

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1                      # the major version this code implements
_PATH = pathlib.Path(__file__).resolve().parent / "data" / "brands" / "brand_registry.json"
# code → 9-digit prefixes, exported by pipeline-v3/export_panel_snapshots.py alongside the registry.
# Ships in this repo for the same reason the registry does: reading it from a box path is the #98 hole.
_CODEMAP_PATH = _PATH.parent / "code_prefixes.json"

_cache: dict | None = None
_codes_cache: dict | None = None
_codes_meta: dict | None = None
_sellable_cache: set | None = None
_sellable_prefix_counts: dict | None = None
_names_cache: dict | None = None


class RegistrySchemaError(RuntimeError):
    """The snapshot declares a major version we do not implement."""


def load(path: pathlib.Path | None = None, *, force: bool = False) -> dict:
    """The snapshot, cached. Refuses a schema major version this code does not implement.

    Refusing beats best-effort parsing: a newer schema that we misread would produce confident wrong
    brands across the catalogue, and every downstream symptom would look like a data problem rather
    than a version mismatch."""
    global _cache
    if _cache is not None and not force and path is None:
        return _cache
    p = path or _PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    got = data.get("schema_version")
    if got != SCHEMA_VERSION:
        raise RegistrySchemaError(
            f"brand_registry.json declares schema_version {got!r}; this panel implements "
            f"{SCHEMA_VERSION}. Refusing to read it — a misread schema would mislabel products "
            f"catalogue-wide and look like a data fault rather than a version mismatch.")
    if path is None:
        _cache = data
    return data


def summary(reg: dict | None = None) -> dict:
    """Headline counts for the registry screen.

    ⚠ `products_under_asserting_prefixes` is NOT the sum of per-prefix counts, and the difference is
    not rounding: 698 products carry barcodes under more than one prefix and are counted under each,
    14 have no usable GTIN and are counted nowhere, and prefixes absent from the registry contribute
    nothing. Summing the column overstates it. Hermes made exactly that mistake while building the
    data, so the honest figure is computed here and the naive sum is deliberately not exposed."""
    reg = reg or load()
    pf = reg.get("prefixes") or {}
    asserting = [v for v in pf.values() if v.get("asserts")]
    suggestions = [v for v in pf.values() if v.get("brand_id") and not v.get("asserts")]
    return {
        "schema_version": reg.get("schema_version"),
        "generated": reg.get("_generated"),
        "brands": len(reg.get("brands") or {}),
        "manufacturers": len(reg.get("manufacturers") or {}),
        "prefixes": len(pf),
        "asserting_prefixes": len(asserting),
        "suggestions": len(suggestions),
        # the figure worth showing for "how much of the catalogue is auto-branded"
        "products_under_asserting_prefixes": sum(v.get("product_count") or 0 for v in asserting),
    }


def brands(reg: dict | None = None) -> list[dict]:
    """Every brand with the prefixes that assert it and how many products those carry — the number
    that turns an edit into an informed decision rather than a guess."""
    reg = reg or load()
    pf = reg.get("prefixes") or {}
    out = []
    for bid, b in (reg.get("brands") or {}).items():
        mine = [(p, v) for p, v in pf.items() if v.get("brand_id") == bid]
        asserting = [(p, v) for p, v in mine if v.get("asserts")]
        out.append({
            "brand_id": bid,
            "canonical": b.get("canonical") or bid,
            "aliases": b.get("aliases") or [],
            "confirmed": bool(b.get("confirmed")),
            "note": b.get("note"),
            "sources": b.get("sources") or [],
            "prefixes": [p for p, _ in mine],
            "asserting_prefixes": [p for p, _ in asserting],
            # blast radius: what turning this brand's assertions off would unbrand
            "product_count": sum((v.get("product_count") or 0) for _, v in asserting),
        })
    out.sort(key=lambda r: (-r["product_count"], r["canonical"].lower()))
    return out


def review_queue(reg: dict | None = None, limit: int = 500) -> list[dict]:
    """Prefixes carrying a brand SUGGESTION that does not assert — the owner's hand-review list.

    This is the load-bearing screen, not a nice-to-have: 465 prefixes hold a machine guess that will
    never label a product until a human agrees. Ordered by how much rides on each decision."""
    reg = reg or load()
    brands_by_id = reg.get("brands") or {}
    mfrs = reg.get("manufacturers") or {}
    rows = []
    for p, v in (reg.get("prefixes") or {}).items():
        if not v.get("brand_id") or v.get("asserts"):
            continue
        b = brands_by_id.get(v["brand_id"]) or {}
        m = mfrs.get(v.get("mfr_id") or "") or {}
        rows.append({
            "prefix": p,
            "brand_id": v["brand_id"],
            # always present, always False here by definition — a field that only appears once
            # overridden would make the client infer state from its absence
            "asserts": bool(v.get("asserts")),
            "canonical": b.get("canonical") or v["brand_id"],
            "legal_brand": v.get("legal_brand"),
            "owner": (m.get("legal_names") or [None])[0],
            "role": m.get("role"),
            "reason": v.get("reason"),
            "src": v.get("src"),
            "confidence": v.get("confidence"),
            "product_count": v.get("product_count") or 0,
            "evidence": (v.get("extra") or {}),
        })
    sc = sellable_prefix_counts()
    for r in rows:
        # None (snapshot missing) stays None — the client shows the registry count alone
        # rather than a fake zero; a missing mapping and "no sellable products" differ.
        r["sellable_count"] = None if sc is None else sc.get(r["prefix"], 0)
    if sc is None:
        rows.sort(key=lambda r: -r["product_count"])
    else:
        # owner rule: review time follows SELLABLE impact; catalogue count breaks ties
        rows.sort(key=lambda r: (-(r["sellable_count"] or 0), -r["product_count"]))
    return rows[:limit]


def codes_map(*, force: bool = False) -> dict | None:
    """The `code → [9-digit prefixes]` snapshot, cached. Returns **None when the snapshot is
    absent** — and callers must surface that state, never treat it as "brand has no products".
    A missing mapping and an unbranded catalogue must not look alike; that distinction is the
    whole lesson of the falsy-guard incidents."""
    global _codes_cache, _codes_meta
    if _codes_cache is not None and not force:
        return _codes_cache
    if not _CODEMAP_PATH.exists():
        log.warning("code_prefixes.json missing at %s — brand→products lists unavailable", _CODEMAP_PATH)
        return None
    data = json.loads(_CODEMAP_PATH.read_text(encoding="utf-8"))
    _codes_cache = data.get("codes") or {}
    _codes_meta = {"generated": data.get("_generated"), "products": data.get("products"),
                   "sellable": len(data.get("sellable") or [])}
    global _sellable_cache, _sellable_prefix_counts, _names_cache
    _sellable_cache = set(data.get("sellable") or [])
    _names_cache = data.get("names") or {}
    _sellable_prefix_counts = None
    return _codes_cache


def snapshot_names() -> dict:
    """Raw Microinvest names shipped with the snapshot (sellable codes) — the last-resort
    name source that guarantees a sellable product never renders nameless."""
    codes_map()
    return _names_cache or {}


def sellable_codes() -> set | None:
    """The sellable-product codes (owner rule: panel counts follow what we can SELL,
    not the full catalogue). None when the snapshot is absent — same honesty contract
    as codes_map()."""
    if codes_map() is None:
        return None
    return _sellable_cache or set()


def sellable_prefix_counts() -> dict | None:
    """prefix -> number of SELLABLE products carrying it. None when the snapshot is
    absent. Cached; rebuilt when the snapshot reloads."""
    global _sellable_prefix_counts
    cmap = codes_map()
    if cmap is None:
        return None
    if _sellable_prefix_counts is None:
        counts: dict = {}
        sell = _sellable_cache or set()
        for code, plist in cmap.items():
            if code in sell:
                for p in plist:
                    counts[p] = counts.get(p, 0) + 1
        _sellable_prefix_counts = counts
    return _sellable_prefix_counts


def codes_meta() -> dict:
    return dict(_codes_meta or {})


def brand_detail(brand_id: str, reg: dict | None = None) -> dict | None:
    """Everything the registry knows about ONE brand, shaped for the edit screen: the brand's own
    fields, every prefix that names it (asserting AND suggestions — visually distinct client-side),
    and the manufacturers behind those prefixes. None for an unknown id."""
    reg = reg or load()
    b = (reg.get("brands") or {}).get(brand_id)
    if b is None:
        return None
    pf = reg.get("prefixes") or {}
    mfrs = reg.get("manufacturers") or {}

    prefix_rows, mfr_ids = [], set()
    for p, v in pf.items():
        if v.get("brand_id") != brand_id:
            continue
        mid = str(v.get("mfr_id") or "")
        if mid:
            mfr_ids.add(mid)
        m = mfrs.get(mid) or {}
        prefix_rows.append({
            "prefix": p,
            "asserts": bool(v.get("asserts")),
            "confidence": v.get("confidence"),
            "reason": v.get("reason"),
            "src": v.get("src"),
            "legal_brand": v.get("legal_brand"),
            "product_count": v.get("product_count") or 0,
            "mfr_id": mid or None,
            "mfr_name": (m.get("legal_names") or [None])[0],
            "mfr_role": m.get("role"),
        })
    # makers that list the brand even without a prefix pointing at it today
    for mid, m in mfrs.items():
        if brand_id in (m.get("brands") or []):
            mfr_ids.add(str(mid))
    prefix_rows.sort(key=lambda r: (-r["product_count"], r["prefix"]))

    manufacturers = []
    for mid in sorted(mfr_ids):
        m = mfrs.get(mid) or {}
        manufacturers.append({
            "mfr_id": mid,
            "name": (m.get("legal_names") or [mid])[0],
            "country": m.get("country"),
            "role": m.get("role"),
            "prefixes": m.get("prefixes") or [],
            "evidence": m.get("evidence") or [],
        })

    return {
        "brand_id": brand_id,
        "canonical": b.get("canonical") or brand_id,
        "aliases": b.get("aliases") or [],
        "confirmed": bool(b.get("confirmed")),
        "kind": b.get("kind"),
        "logo": b.get("logo"),
        "sources": b.get("sources") or [],
        "note": b.get("note"),
        "prefixes": prefix_rows,
        "manufacturers": manufacturers,
        # blast radius of the brand as a whole: what its asserting prefixes currently label
        "product_count": sum(r["product_count"] for r in prefix_rows if r["asserts"]),
    }
