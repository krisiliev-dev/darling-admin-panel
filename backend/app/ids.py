# -*- coding: utf-8 -*-
"""One place to normalise a Shopify order id across the panel→connector boundary.

Two id-spaces meet here and they are NOT interchangeable: the panel's own state (fulfillment_state,
cod_settlement) and Shopify itself key an order by its GID — `gid://shopify/Order/123` — while the
connector's order log (ORDERS_STATE.jsonl, written from the webhook payload's numeric `id`) keys on
the BARE NUMERIC id — `123`. Hand a GID to a connector call and its `_seen_order` lookup simply
misses: the call returns a polite "no_logged_order" and writes NOTHING, while the panel happily
reports success. Normalise at EVERY panel→connector call site.
"""


def numeric_oid(v) -> str:
    """The bare numeric order id — `gid://shopify/Order/123` → `123`; an already-bare id is
    returned unchanged, so it's safe to apply on both sides of a join."""
    return str(v or "").rsplit("/", 1)[-1]
