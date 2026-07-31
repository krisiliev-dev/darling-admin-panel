# -*- coding: utf-8 -*-
"""Detect when the panel is running SHARED CORE code that differs from the copy in this repo.

Why this exists (2026-07-31). `darling_core` prefers the sibling `../agent-pipeline/*.py` when it is
present and falls back to `darling_core/_vendor` only when it is not. On the server the sibling IS
present — but agent-pipeline is not part of this repository, so `git pull` in the deploy updates
`_vendor` and leaves the sibling untouched.

The consequence was found the hard way: a fix to `courier.py` passed CI (CI has no sibling, so it tests
`_vendor`), deployed successfully, reported healthy — and did not run. Production kept quoting the old
COD price. **CI green + deploy green + health green + change absent** is the most dangerous shape in
this codebase, because every signal a human would check says yes.

This module does NOT try to fix the drift by overwriting anything: the sibling is the enrichment
pipeline's own working copy and another agent edits it live, so clobbering it would trade a silent
failure for a destructive one. It makes the drift VISIBLE — in /api/health, in the startup log, and
(via the caller) in Проблеми — on the principle that has held all week: a wrong value nobody can see is
worse than a loud one.
"""
import hashlib
import logging
import pathlib

log = logging.getLogger(__name__)

_VENDOR = pathlib.Path(__file__).resolve().parents[2] / "darling_core" / "_vendor"


def _sha(p: pathlib.Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def core_drift() -> list[dict]:
    """Modules whose LOADED file differs from this repo's vendored copy.

    Compares the file each shared module was actually imported from against `_vendor/<name>.py`.
    Returns [] when they agree (including the CI case, where the loaded file IS the vendored one).
    Never raises — a diagnostic that can break startup is worse than the drift it reports.
    """
    out: list[dict] = []
    try:
        # via app.core, which performs the sys.path setup that makes darling_core importable at all
        from .core import analytics, barsy, connector, courier, db, shopify, shopify_orders
        loaded = {
            "analytics": analytics, "barsy_api": barsy, "courier": courier, "db": db,
            "shopify_api": shopify, "barsy_shopify_connector": connector,
            "shopify_orders": shopify_orders,
        }
        for name, mod in loaded.items():
            src = getattr(mod, "__file__", "") or ""
            vend = _VENDOR / f"{name}.py"
            if not src or not vend.exists():
                continue
            live, snap = pathlib.Path(src).resolve(), vend.resolve()
            if live == snap:
                continue                            # already running the repo's copy (CI, or forced)
            h_live, h_snap = _sha(live), _sha(snap)
            if h_live and h_snap and h_live != h_snap:
                out.append({"module": name, "loaded_from": str(live),
                            "loaded_sha": h_live, "repo_sha": h_snap})
    except Exception as e:  # noqa: BLE001 — never let the check itself break the health probe
        # …but NEVER report "no drift" because the CHECK failed. A monitor that returns clean on its
        # own error is the exact bug it exists to catch — the first version of this function did
        # precisely that, silently returning [] when an import failed.
        log.exception("core drift check failed")
        return [{"module": "(check failed)", "loaded_from": "", "loaded_sha": "",
                 "repo_sha": "", "error": str(e)[:200]}]
    return out


def log_core_drift() -> list[dict]:
    """Run the check at startup and say so loudly. Returns the drift list for the caller to record."""
    drift = core_drift()
    if drift:
        log.error(
            "CORE DRIFT: %d shared module(s) differ from this repo's _vendor — the panel is running "
            "code the deploy did NOT update: %s. Fixes to these will pass CI, deploy green, and NOT "
            "take effect. See task #98.",
            len(drift), ", ".join(d["module"] for d in drift))
    else:
        log.info("core drift check: shared modules match the repo")
    return drift
