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
# Reorg 2026-08-01: on dev machines the connector modules were extracted out of
# agent-pipeline into a sibling `connector/` (C:\Users\lora_\darling\connector), and
# pipeline.db moved to a sibling `data/`. The box keeps the old layout until Phase B,
# so BOTH spellings are probed, old one first — behaviour there is unchanged.
_CONNECTOR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "connector"))
_DATA_DB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "pipeline.db"))
_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")
# MODULE PATH and DATA ROOT are deliberately SEPARATE (task #98, 2026-07-31).
#
# They used to be one directory, which was a live deployment hole: imports preferred the sibling
# ../agent-pipeline, that directory is not in this repository, and NOTHING deploys it (verified: no
# cron, no systemd unit, its local .git has no remote and a 19 July HEAD). It was updated by hand.
# So a fix landed in _vendor, passed CI, deployed green, reported healthy — and production kept running
# the old module. A stale courier.py under-charged the COD surcharge on every Speedy order that way.
#
# IMPORTS therefore come from _vendor: it ships in this repo, so it cannot be stale relative to what CI
# tested and the deploy carried. agent-pipeline stays on the path AFTER it, so modules we do not vendor
# still import — it simply no longer shadows the ones we do.
#
# ⚠ DATA is a separate question and the first attempt at this got it wrong. `db.py` resolves pipeline.db
# next to ITSELF, never consulting CORE_ROOT, so moving the import moved the data lookup with it and
# broke every catalogue read. It now honours a PIPELINE_DB env var (set in docker-compose) and REFUSES
# to create a missing file there — an explicitly configured path that does not exist is always a
# misconfiguration, never a first run, and sqlite would otherwise leave an empty database that reports
# "no such table" today and an empty CATALOGUE after any migration.
if os.path.isdir(_AGENT_PIPELINE):
    CORE_ROOT = _AGENT_PIPELINE          # box (until Phase B): old layout, data lives here
elif os.path.isdir(_CONNECTOR):
    CORE_ROOT = _CONNECTOR               # reorged dev machine (and box after Phase B)
else:
    CORE_ROOT = _VENDOR                  # standalone checkout (CI)

_MODULE_DIR = _VENDOR if os.path.isdir(_VENDOR) else CORE_ROOT               # code comes from here
if _MODULE_DIR in sys.path:
    sys.path.remove(_MODULE_DIR)
sys.path.insert(0, _MODULE_DIR)                    # first: this repo's copy wins
if CORE_ROOT not in sys.path:
    sys.path.append(CORE_ROOT)                     # last: non-vendored siblings still importable

# Point db.py at the DATA root explicitly. Without this the split works only where something else sets
# PIPELINE_DB (the container) and breaks everywhere else — dev machines included, which is how the first
# attempt reached production: it was never exercised anywhere the env var was absent. Setting it here
# makes the module path and the data root consistent by construction rather than by deployment config.
# Only when the sibling EXISTS: with no sibling (CI) there is no pipeline.db to point at, and db.py
# refuses a configured-but-missing path by design, so we leave it on its own default there.
if os.path.isdir(_AGENT_PIPELINE):
    os.environ.setdefault("PIPELINE_DB", os.path.join(_AGENT_PIPELINE, "pipeline.db"))
elif os.path.isfile(_DATA_DB):
    # reorged layout: the catalogue lives in ../../data/pipeline.db. Only set when the
    # file actually EXISTS — db.py refuses a configured-but-missing path by design, and
    # with no sibling data at all (CI) we leave db.py on its own default exactly as before.
    os.environ.setdefault("PIPELINE_DB", _DATA_DB)

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
