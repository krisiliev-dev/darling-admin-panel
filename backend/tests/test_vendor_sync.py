# -*- coding: utf-8 -*-
"""_vendor must not silently drift from the connector that actually runs.

At runtime `darling_core` prefers the real `../agent-pipeline` sibling and only falls back to
`darling_core/_vendor` when it's absent — which is exactly what happens in CI, since agent-pipeline is a
SEPARATE repo that CI never checks out. So `_vendor` is a hand-maintained snapshot, and when it goes stale
CI happily tests a connector that no longer resembles production.

That is not hypothetical: on 2026-07-28 the committed `_vendor` still held the pre-paid-model connector
(no COD_PAYMETHOD_ID, no list_cod_accounts) while the box ran the new one — every CI run had been
green against the wrong code.

CI can't compare the two (it has only one of them), so this is a LOCAL tripwire: it runs on a dev machine
where both trees exist and SKIPS where the sibling is absent. Cheap, and it catches the drift at the only
moment anyone can act on it — before the commit.
"""
import hashlib
import pathlib

import pytest

_HERE = pathlib.Path(__file__).resolve()
_ADMIN_PANEL = _HERE.parents[2]                     # …/admin-panel
_VENDOR = _ADMIN_PANEL / "darling_core" / "_vendor"

# The connector has lived in two places. Pre-reorg it was the `agent-pipeline` sibling, which is
# still the layout on the PANEL BOX; the 2026-08-01 reorg moved the laptop copy to `connector`.
# Only checking the old name meant this test skipped on the laptop as well as in CI from the reorg
# onward — and a skip is green, so the drift it exists to catch went unnoticed until 2026-08-08,
# when production turned out to be running a barsy_api without the uncertain-write protection that
# CI had been testing. Check both, and make the skip reason say which.
_CANDIDATES = [_ADMIN_PANEL.parent / "agent-pipeline",   # pre-reorg / panel box layout
               _ADMIN_PANEL.parent / "connector"]        # post-reorg laptop layout
_SIBLING = next((p for p in _CANDIDATES if p.is_dir()), _CANDIDATES[0])

# the modules darling_core re-exports; a mismatch in any of them changes runtime behaviour
_MIRRORED = [
    "barsy_shopify_connector.py", "barsy_api.py", "shopify_api.py", "shopify_orders.py",
    "courier.py", "db.py", "analytics.py", "barsy_push.py", "barsy_shopify_sync.py",
]


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.skipif(not _SIBLING.is_dir(),
                    reason=f"connector tree absent — looked for {[str(p) for p in _CANDIDATES]}; "
                           "nothing to compare against (expected in CI, NOT on a dev machine)")
@pytest.mark.parametrize("name", _MIRRORED)
def test_vendor_matches_the_runtime_connector(name):
    vendored, live = _VENDOR / name, _SIBLING / name
    if not live.exists():
        pytest.skip(f"{name} not present in agent-pipeline")
    assert vendored.exists(), (
        f"{name} exists in agent-pipeline but NOT in _vendor — CI would import a missing module. "
        f"Copy it: cp ../agent-pipeline/{name} darling_core/_vendor/{name}")
    assert _sha(vendored) == _sha(live), (
        f"_vendor/{name} has DRIFTED from agent-pipeline/{name}.\n"
        f"CI tests _vendor while the box runs agent-pipeline, so this drift means CI is validating code "
        f"that is not what ships. Re-sync before committing:\n"
        f"    CHECK WHICH SIDE IS NEWER FIRST - do not copy on autopilot.\n"
        f"    Usually _vendor is the stale snapshot and the connector wins:\n"
        f"        cp <connector>/{name} darling_core/_vendor/{name}\n"
        f"    But on 2026-08-08 it was the OTHER way round: _vendor held the uncertain-write\n"
        f"    protection and the connector - which is what production runs - did not, so copying\n"
        f"    in the usual direction would have deleted it from CI too. Diff them and decide.")
