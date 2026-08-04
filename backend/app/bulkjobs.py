# -*- coding: utf-8 -*-
"""In-memory registry for long-running bulk Barsy write jobs (G4c).

A bulk price/rename over hundreds of products is a serial, paced chain of Articles_update calls that
can run minutes — far past an HTTP request's patience. Above a small cap the router runs it as a
background asyncio task and returns a job id; the frontend polls `GET /catalog/bulk-jobs/{id}` for
progress. State lives in this module's dict (the panel is a single uvicorn worker) — NOT persisted,
so a restart drops in-flight jobs; that's acceptable for an interactive op the operator can re-run.
"""
import uuid

_JOBS: dict[str, dict] = {}
_MAX_JOBS = 200                 # bound growth; prune finished jobs past this


def create(op: str, total: int) -> str:
    """Register a new running job, return its id."""
    jid = uuid.uuid4().hex
    _JOBS[jid] = {"id": jid, "op": op, "total": int(total), "done": 0,
                  "applied": 0, "skipped": 0, "failed": [], "status": "running", "note": ""}
    if len(_JOBS) > _MAX_JOBS:
        for k, v in list(_JOBS.items()):
            if v["status"] != "running":
                _JOBS.pop(k, None)
            if len(_JOBS) <= _MAX_JOBS:
                break
    return jid


def get(jid: str) -> dict | None:
    return _JOBS.get(jid)


def bump(jid: str, *, applied: int = 0, skipped: int = 0, failed_code: str | None = None,
         count: int = 1) -> None:
    """Record processed item(s): advance `done` by `count` + the applied/skipped/failed tallies.
    `count` lets a one-shot native write (save_mass) report N items at once, not N calls."""
    j = _JOBS.get(jid)
    if not j:
        return
    j["done"] += count
    j["applied"] += applied
    j["skipped"] += skipped
    if failed_code is not None:
        j["failed"].append(failed_code)


def finish(jid: str, status: str = "done", note: str = "") -> None:
    j = _JOBS.get(jid)
    if j:
        j["status"] = status
        j["note"] = note
