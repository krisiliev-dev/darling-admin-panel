# -*- coding: utf-8 -*-
"""Shared helpers for bulk Barsy write loops — pacing + batch baseline fetch (G4 resilience).

The low-tier Barsy server throttles write bursts (throws 501 / "сървъра е зает"). `barsy_api.call()`
already retries those, but that's *recovery* after a failure. These two helpers attack the cause:

  * `pace()` spaces out serial `Articles_update` calls so a bulk apply doesn't burst the server.
  * `price_baselines()` replaces one `Articles_get` per item (the 2N N+1 in relative price ops) with
    a few `Articles_getlist` pages — Barsy has no per-id batch get, but getlist accepts an id list.
"""
import asyncio

from ..core import barsy

# Inter-write delay for serial Articles_update loops. ~8 writes/sec: light enough that a few-hundred-
# row apply doesn't crawl, heavy enough to keep the low tier from tipping into a 501 burst. (Large
# batches still want the G4c background-job path — this only smooths the synchronous case.)
PACE_S = 0.12


async def pace() -> None:
    """Sleep one pacing interval between serial Barsy writes (non-blocking — yields the event loop)."""
    await asyncio.sleep(PACE_S)


def price_baselines(ids, chunk: int = 500) -> dict[int, float | None]:
    """{article_id: actual_price} for the given ids, in as few round-trips as possible.

    Kills the per-item `Articles_get` in relative bulk-price ops: `Articles_getlist` takes a
    `search_article_ids` list (and orders results by it). NB `actual_price` DEFAULTS to off on this
    install and must be requested via `extra_properties:{actual_price:1}` (same as the cache sync) —
    without it every row's actual_price is null → every relative price op (and the online-price % / ±
    modes) sees "no baseline". Ids with no row (or an unparseable price) map to None so the caller can
    fall back to its cache baseline. Never raises — a failed page yields no baselines for that chunk
    (→ safe fallback), not an error."""
    out: dict[int, float | None] = {}
    id_list = [int(i) for i in ids]
    for i in range(0, len(id_list), chunk):
        batch = id_list[i:i + chunk]
        try:
            rows = barsy.call("Articles_getlist",
                              {"filters": {"search_article_ids": batch}, "length": len(batch),
                               "extra_properties": {"actual_price": 1}}) or []
        except Exception:
            rows = []
        for a in rows:
            aid = a.get("article_id")
            if aid is None:
                continue
            try:
                out[int(aid)] = float(a.get("actual_price"))
            except (TypeError, ValueError):
                out[int(aid)] = None
    return out


# --- native mass save (save_mass) -----------------------------------------------------------------
# Barsy's article grid persists edits with a single `articles_index` save_mass action rather than one
# Articles_update per row. Verified reachable over the connector's Basic-auth path
# (/endpoints/json/articles_index?bid=1) on 2026-08-04: one transaction for N cells across N articles,
# which sidesteps the very write-burst throttling that pace() exists to smooth -- it is what Barsy's
# own admin UI does. NB save_mass writes the GRID COLUMN only; it does NOT touch coupled fields such
# as article_name_ml.bg_BG (verified stale after a save_mass rename) -- use Articles_update for fields
# with multilang/derived siblings. For plain scalar columns (prices, flags, codes) it is exact.
_MASS_STRUCT = "eStructList_1"
_MASS_CHUNK = 500          # cells per call; a huge apply is chunked + paced so the low tier is not
                           # handed a multi-thousand-cell body in one shot.


def _mass_cell(article_id, col_name, value, native_value=None) -> tuple[str, dict]:
    aid = int(article_id)
    key = f"articles_index_{_MASS_STRUCT}_{aid}_{col_name}"
    return key, {"row_id": f"articles_index_{_MASS_STRUCT}_{aid}", "col_name": col_name,
                 "value": value, "native_value": (value if native_value is None else native_value),
                 "article_id": str(aid)}


def save_mass_call(values: dict, bid: int = 1):
    """POST one save_mass with an already-built `values` map. Raises BarsyError on failure."""
    return barsy.call("articles_index",
                      {"action_type": "save_mass", "active_struct_id": _MASS_STRUCT, "values": values},
                      bid=bid)


def save_mass(changes, chunk: int = _MASS_CHUNK, bid: int = 1) -> int:
    """Persist `changes` via native save_mass, chunked + paced. Each change: a dict with article_id,
    col_name, value (and optional native_value -- Barsy ignores it, verified, so it is wire fidelity
    only). Returns the count of cells written. Barsy owns each chunk as one transaction; a failed
    chunk raises (earlier chunks stay applied). Sync -- call via run_in_threadpool."""
    import time
    cells = [_mass_cell(c["article_id"], c["col_name"], c["value"], c.get("native_value"))
             for c in changes]
    written = 0
    for i in range(0, len(cells), chunk):
        batch = dict(cells[i:i + chunk])
        if i:
            time.sleep(PACE_S)          # space chunks so a large apply does not burst the low tier
        save_mass_call(batch, bid=bid)
        written += len(batch)
    return written


def field_baselines(ids, col: str, chunk: int = 500) -> dict:
    """{article_id: float(col)} for a numeric grid column, via Articles_getlist pages (no per-id get).
    Generalises price_baselines to any numeric column so relative mass ops have a live baseline. Ids
    with no row or an unparseable value map to None. Never raises."""
    out: dict = {}
    id_list = [int(i) for i in ids]
    for i in range(0, len(id_list), chunk):
        batch = id_list[i:i + chunk]
        try:
            rows = barsy.call("Articles_getlist",
                              {"filters": {"search_article_ids": batch}, "length": len(batch),
                               "extra_properties": {col: 1}}) or []
        except Exception:
            rows = []
        for a in rows:
            aid = a.get("article_id")
            if aid is None:
                continue
            try:
                out[int(aid)] = float(a.get(col))
            except (TypeError, ValueError):
                out[int(aid)] = None
    return out
