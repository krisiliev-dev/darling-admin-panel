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

# make the existing agent-pipeline modules importable without moving them
_AGENT_PIPELINE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "agent-pipeline"))
if _AGENT_PIPELINE not in sys.path:
    sys.path.insert(0, _AGENT_PIPELINE)

CORE_ROOT = _AGENT_PIPELINE          # where the real code + pipeline.db live, for now

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
