# -*- coding: utf-8 -*-
"""darling_core — the shared business-core for the Darling systems (Phase 0, 2026-07-20).

ONE import surface that both apps build against:
  • the (temporary) reviewer site  — will be retired after the enrichment backlog is done
  • the (permanent) admin panel     — the daily-driver, built on FastAPI + React

WHY A FACADE FIRST: the real modules (db, barsy_api, shopify_api, courier, analytics, the
enrichment pipeline) live in ../agent-pipeline and are imported by LIVE processes right now
(the enrichment + review fleets, the review server). Physically moving them would break those
running processes. So Phase 0 re-exports them from here WITHOUT moving anything — establishing
the clean boundary the admin panel builds against. When the backlog is done and processes can
safely restart, the files relocate INTO this package and agent-pipeline keeps thin shims.

Usage (from the admin-panel backend):
    from darling_core import db, barsy, shopify, courier, analytics

Each re-export is the existing, battle-tested module — same behaviour, cleaner name.
"""
import os
import sys

# make the existing agent-pipeline modules importable without moving them.
# Prefer the real ../../agent-pipeline sibling (server + dev, where pipeline.db also lives). When it
# isn't present — a standalone checkout of just this repo, e.g. CI — fall back to the bundled
# snapshot of the SAME client modules in ./_vendor, so the panel still imports. The server never
# uses _vendor (its sibling exists); keep _vendor in sync if those 9 modules change.
_AGENT_PIPELINE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "agent-pipeline"))
_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")
# MODULE PATH and DATA ROOT are deliberately SEPARATE (2026-07-31, task #98).
#
# They used to be the same directory, and that was a live deployment hole: this package preferred the
# sibling ../agent-pipeline for imports, agent-pipeline is not in this repository, and NOTHING deploys
# it (verified — no cron, no systemd unit, and its local .git has no remote and a 19 July HEAD). It was
# updated by hand, by two people remembering to scp. So a fix to a shared module landed in _vendor,
# passed CI, deployed green, reported healthy — and production kept running the old code. A stale
# courier.py under-charged the COD surcharge on every Speedy order for an unknown period that way.
#
# Two people remembering to copy files is not a deployment mechanism; it is the absence of one.
#
# So: IMPORTS come from _vendor, which ships in this repo and therefore cannot be stale relative to what
# CI tested. DATA still comes from the sibling, because pipeline.db genuinely lives there and is not
# ours to relocate. agent-pipeline stays on the path AFTER _vendor so modules we do not vendor still
# resolve — it just no longer shadows the ones we do.
CORE_ROOT = _AGENT_PIPELINE if os.path.isdir(_AGENT_PIPELINE) else _VENDOR   # data (pipeline.db)

_MODULE_DIR = _VENDOR if os.path.isdir(_VENDOR) else CORE_ROOT               # code (this repo's copy)
if _MODULE_DIR in sys.path:
    sys.path.remove(_MODULE_DIR)
sys.path.insert(0, _MODULE_DIR)                    # first: vendored modules win
if CORE_ROOT not in sys.path:
    sys.path.append(CORE_ROOT)                     # last: non-vendored siblings still importable

# --- the shared surface -------------------------------------------------------------
import db                     # noqa: E402  system-of-record (SQLite; the one DB the panel reads)
import barsy_api as barsy     # noqa: E402  Barsy ERP client (articles, stock, suppliers)
import shopify_api as shopify # noqa: E402  Shopify Admin GraphQL client (products, orders)
import courier                # noqa: E402  courier-agnostic shipping (Speedy/Econt/BoxNow/Sameday/…)
import analytics              # noqa: E402  star-schema ETL + KPI queries for insights

# enrichment (product intake) is heavier and tied to the fleet harness — wrapped in Phase 2
# via a thin `intake` module here rather than exposing run_one/pipeline_v2 wholesale.

__all__ = ["db", "barsy", "shopify", "courier", "analytics", "CORE_ROOT"]


def health():
    """Quick check that the core surface imports and the DB is reachable — used by the panel's
    startup probe."""
    out = {"core_root": CORE_ROOT}
    try:
        con = db.connect()
        out["db_products"] = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        con.close()
        out["db"] = "ok"
    except Exception as e:
        out["db"] = f"error: {str(e)[:80]}"
    out["couriers"] = [n for n, _b, _ok in courier.available()]
    return out
