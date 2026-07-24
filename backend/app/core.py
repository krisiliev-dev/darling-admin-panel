# -*- coding: utf-8 -*-
"""Bridge to the shared business-core. Every route reaches the catalog / Barsy / Shopify /
couriers / analytics THROUGH here — never by importing agent-pipeline modules directly.
Reads of pipeline.db go through darling_core.db; the panel never writes it in Phase 1."""
import os
import sys

# put admin-panel/ on the path so `import darling_core` resolves
_ADMIN_PANEL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ADMIN_PANEL not in sys.path:
    sys.path.insert(0, _ADMIN_PANEL)

import darling_core  # noqa: E402
from darling_core import db, barsy, shopify, courier, analytics  # noqa: E402,F401

# order reader + fulfillment-state store (prototype in agent-pipeline; wrapped for the panel)
import shopify_orders  # noqa: E402,F401

# Barsy⇄Shopify connector LOGIC (translation fns); the HTTP hosting layer lives in routers/webhooks.py.
# darling_core already put agent-pipeline/ on sys.path, so this sibling module resolves.
import barsy_shopify_connector as connector  # noqa: E402,F401

health = darling_core.health
