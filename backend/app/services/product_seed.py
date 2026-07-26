# -*- coding: utf-8 -*-
"""The bundled historical product seed (data/seed_products.json) — a read-only catalogue used as an
interim match/insight source pre-launch. Extracted from routers/inventory_health so zayavki +
intake_delivery use it without importing a router (review: no service layer)."""
import json
import os
from functools import lru_cache

_PRODUCTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed_products.json")


@lru_cache(maxsize=1)
def products() -> list:
    try:
        with open(_PRODUCTS_PATH, encoding="utf-8") as f:
            return json.load(f).get("products", [])
    except (OSError, json.JSONDecodeError):
        return []
