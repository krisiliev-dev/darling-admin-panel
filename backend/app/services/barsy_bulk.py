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
    `search_article_ids` list (and orders results by it), and returns `actual_price` in the default
    `common` payload — no extra_properties needed (proven by pricing.apply_supplier). Ids with no row
    (or an unparseable price) map to None so the caller can fall back to its cache baseline. Never
    raises — a failed page yields no baselines for that chunk (→ safe fallback), not an error."""
    out: dict[int, float | None] = {}
    id_list = [int(i) for i in ids]
    for i in range(0, len(id_list), chunk):
        batch = id_list[i:i + chunk]
        try:
            rows = barsy.call("Articles_getlist",
                              {"filters": {"search_article_ids": batch}, "length": len(batch)}) or []
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
