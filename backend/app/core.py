# -*- coding: utf-8 -*-
"""Bridge to the shared business-core. Every route reaches the catalog / Barsy / Shopify /
couriers / analytics THROUGH here — never by importing agent-pipeline modules directly.
Reads of pipeline.db go through darling_core.db; the panel never writes it in Phase 1."""
# ruff: noqa: I001  — import ORDER is load-bearing here (path setup → darling_core → its siblings);
# do NOT let the import-sorter alphabetise these or the sibling imports fail (agent-pipeline is only
# on sys.path AFTER darling_core is imported).
import os
import sys

# put admin-panel/ on the path so `import darling_core` resolves
_ADMIN_PANEL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ADMIN_PANEL not in sys.path:
    sys.path.insert(0, _ADMIN_PANEL)

import darling_core  # noqa: E402
from darling_core import analytics, barsy, courier, db, shopify  # noqa: E402,F401

# Barsy⇄Shopify connector LOGIC + order reader — siblings in agent-pipeline, which darling_core just
# put on sys.path. The HTTP hosting layer for the connector lives in routers/webhooks.py.
import barsy_shopify_connector as connector  # noqa: E402,F401
import shopify_orders  # noqa: E402,F401

health = darling_core.health

from .config import get_settings  # noqa: E402


def barsy_write_live() -> bool:
    """MASTER GATE for the panel's direct MUTATING catalog/pricing/purchasing Barsy writes
    (bulk price/rename, promo, single-article edit, pricing-rule apply, PO create/close).
    Default OFF (pre-launch) so testing Ценообразуване / bulk-rename can't rewrite the live
    catalog. Reads are NEVER gated; intake/delivery/webhooks keep their own separate gates."""
    return get_settings().barsy_write_live
