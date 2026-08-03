# -*- coding: utf-8 -*-
"""Simplified brand registry (v2) — the SINGLE editable source of truth (owner 2026-08-02).

SEED vs LIVE: the pipeline ships `brand_registry_v2.seed.json` (tracked). The app keeps a LIVE
`brand_registry_v2.json` (git-ignored) and every edit writes the LIVE file, so a deploy never
clobbers owner edits.

The seed is NOT a one-shot copy. It used to be — and that silently pinned the panel to whatever
the pipeline shipped the first time the app ran: after the 2026-08-03 registry review the seed
carried 246 brands while the live file still had 229, so 28 brands the pipeline had just fixed
were invisible in the panel and nothing said so. Now every load folds a changed seed in, under
one rule: **the human always wins.**

  - a brand the owner has EDITED (`edited: true`) is never touched by a seed refresh;
  - a brand the owner has DELETED is remembered in `deleted[]` and is never resurrected by a
    refresh — only an explicit create() brings it back;
  - everything else is machine-owned and simply follows the pipeline.

Registries written before the `edited` flag existed carry no ownership information, so the first
migration infers it CONSERVATIVELY: any live brand that differs from its seed entry (or has no
seed entry at all) is assumed human-touched and frozen. That can leave a stale brand stale until
someone opens it, which is recoverable; guessing the other way would overwrite an owner edit,
which is not.

Reads/edits are serialized with a reentrant lock; writes are atomic (temp + replace).
Resolution is two-path (prefix OR alias-in-name); product counts come from confirmed_brands.json.
"""
import hashlib
import json
import logging
import os
import pathlib
import tempfile
import threading
from collections import Counter

log = logging.getLogger(__name__)

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "brands"
_SEED = _DATA / "brand_registry_v2.seed.json"
_LIVE = pathlib.Path(os.environ.get("BRAND_REGISTRY_PATH") or (_DATA / "brand_registry_v2.json"))
_CONF = _DATA / "confirmed_brands.json"
# Reentrant: _load() takes the lock to persist a seed merge, and edit()/create()/delete() call
# _load() while already holding it.
_LOCK = threading.RLock()
SCHEMA_VERSION = 2

_STAMP = "seed_digest"      # which seed the live file has already folded in
_SEED_FIELDS = ("canonical", "aliases", "prefixes", "series", "gs1_owner", "logo")


def _ensure_live() -> None:
    """An empty live registry — the seed merge below fills it. Deliberately NOT a copy of the
    seed: one code path does the folding, first run and every run after."""
    if not _LIVE.exists():
        _LIVE.parent.mkdir(parents=True, exist_ok=True)
        _LIVE.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "brands": {}},
                                    ensure_ascii=False), encoding="utf-8")


def _digest(p: pathlib.Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _same_as_seed(live_b: dict, seed_b: dict) -> bool:
    def norm(b, f):
        v = b.get(f)
        return sorted(v) if isinstance(v, list) else v
    return all(norm(live_b, f) == norm(seed_b, f) for f in _SEED_FIELDS)


def _merge_seed(reg: dict) -> bool:
    """Fold a changed seed into `reg` in place. Returns True when `reg` was modified.

    A missing or unreadable seed changes nothing — the live registry is left exactly as it is.
    """
    if not _SEED.exists():
        return False
    dig = _digest(_SEED)
    if not dig or reg.get(_STAMP) == dig:
        return False
    try:
        seed_brands = (json.loads(_SEED.read_text(encoding="utf-8")).get("brands") or {})
    except (OSError, ValueError) as e:
        log.warning("brand seed unreadable (%s) — live registry left untouched.", e)
        return False

    live = reg.setdefault("brands", {})
    gone = set(reg.get("deleted") or [])
    first_migration = _STAMP not in reg

    if first_migration and live:
        # No ownership information exists yet. Freeze anything that is not byte-for-byte what the
        # seed says, so an owner edit made before this flag existed cannot be overwritten.
        frozen = 0
        for bid, lb in live.items():
            if lb.get("edited"):
                continue
            sb = seed_brands.get(bid)
            if sb is None or not _same_as_seed(lb, sb):
                lb["edited"] = True
                frozen += 1
        if frozen:
            log.info("brand registry: first seed merge — %d brand(s) differ from the seed and were "
                     "assumed hand-edited (frozen).", frozen)

    added = refreshed = 0
    for bid, sb in seed_brands.items():
        if bid in gone:
            continue
        cur = live.get(bid)
        if cur is None:
            live[bid] = dict(sb)
            added += 1
        elif not cur.get("edited"):
            live[bid] = dict(sb)
            refreshed += 1
    reg[_STAMP] = dig
    if added or refreshed:
        log.info("brand registry: seed %s folded in — %d new, %d refreshed, %d owner-edited kept, "
                 "%d deleted kept out.", dig, added, refreshed,
                 sum(1 for b in live.values() if b.get("edited")), len(gone))
    return True


def _load() -> dict:
    _ensure_live()
    with _LOCK:
        reg = json.loads(_LIVE.read_text(encoding="utf-8"))
        if _merge_seed(reg):
            _save(reg)
        return reg


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
        # so the page can say WHERE a row came from instead of the owner wondering why a pipeline
        # fix is not visible
        "owner_edited": sum(1 for b in brands.values() if b.get("edited")),
        "deleted": len(reg.get("deleted") or []),
    }


def _row(bid, b, prod):
    return {"id": bid, "canonical": b["canonical"], "aliases": b.get("aliases") or [],
            "prefixes": b.get("prefixes") or [], "series": b.get("series") or [],
            "gs1_owner": b.get("gs1_owner"), "logo": b.get("logo"),
            "edited": bool(b.get("edited")),
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
    Prefixes are digit-only 9-char; aliases are lowercased. canonical renames the display.

    Any successful patch marks the brand owner-edited, which takes it out of the seed's hands
    for good — the pipeline can no longer overwrite a decision a human made."""
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
        b["edited"] = True
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
                              "gs1_owner": None, "logo": None, "edited": True}
        # an explicit re-create is the ONE thing that lifts a tombstone
        if bid in (reg.get("deleted") or []):
            reg["deleted"] = [x for x in reg["deleted"] if x != bid]
        _save(reg)
        return _row(bid, reg["brands"][bid], Counter())


def delete(bid: str) -> None:
    """Remove a brand AND remember that it was removed. Without the tombstone the next seed
    refresh would quietly bring it back, which is how a deletion turns into a haunting."""
    with _LOCK:
        reg = _load()
        if bid not in reg.get("brands", {}):
            raise KeyError(bid)
        del reg["brands"][bid]
        gone = list(reg.get("deleted") or [])
        if bid not in gone:
            gone.append(bid)
        reg["deleted"] = sorted(gone)
        _save(reg)
