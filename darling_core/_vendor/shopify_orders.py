# -*- coding: utf-8 -*-
"""Shopify order reader + fulfillment-state store for the ops console (owner 2026-07-19).

Pulls orders from the Shopify Admin API (GraphQL) and normalizes them into the shape the
fulfillment console needs; keeps OUR operational status per order in the DB (Shopify's own
fulfillment status is coarse and knows nothing about COD payout or which courier we used).

Our status pipeline:  new → picking → packed → shipped → delivered → cod_paid → returned
"""
import json
import time

import db

_ORDERS_Q = """
{ orders(first: %d, sortKey: CREATED_AT, reverse: true %s) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name createdAt displayFulfillmentStatus displayFinancialStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      paymentGatewayNames
      note
      customer { firstName lastName }
      shippingAddress { name phone address1 address2 city zip province country }
      lineItems(first: 50) { edges { node {
        title quantity sku
        variant { id sku barcode }
      } } }
    } }
} }
"""

STATUSES = ["new", "picking", "packed", "shipped", "delivered", "cod_paid", "returned"]
STATUS_BG = {"new": "нова", "picking": "комплектоване", "packed": "опакована",
             "shipped": "изпратена", "delivered": "доставена", "cod_paid": "платена (НП)",
             "returned": "върната"}


def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS fulfillment_state(
        order_id TEXT PRIMARY KEY, order_name TEXT, status TEXT, courier TEXT,
        tracking TEXT, cod_amount REAL, label_ref TEXT, updated_ts TEXT, note TEXT)""")


def fetch_orders(limit=100, unfulfilled_only=False):
    """Normalized recent orders from Shopify. COD is inferred from the payment gateway /
    financial status (наложен платеж shows as a manual/COD gateway, unpaid at creation)."""
    import shopify_api
    # Always exclude CANCELLED orders — a cancelled order can still read fulfillment_status:unfulfilled,
    # so without this it lingers in the panel's Онлайн поръчки queue after the customer/staff cancel it.
    q_terms = ["-status:cancelled"]
    if unfulfilled_only:
        q_terms.append("fulfillment_status:unfulfilled")
    filt = ', query: "%s"' % " AND ".join(q_terms)
    data = shopify_api.gql(_ORDERS_Q % (limit, filt))
    # shopify_api.gql() already returns the UNWRAPPED `data` payload (top-level key `orders`), so do NOT
    # unwrap `data` again — the old `.get("data")` here double-unwrapped → always 0 orders → the panel's
    # Онлайн поръчки queue showed empty ("магазинът не е пуснат") for every real order. (fixed 2026-07-28)
    edges = ((data or {}).get("orders") or {}).get("edges") or []
    out = []
    for e in edges:
        n = e["node"]
        addr = n.get("shippingAddress") or {}
        cust = n.get("customer") or {}
        total = (((n.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")) or "0"
        gateways = [g.lower() for g in (n.get("paymentGatewayNames") or [])]
        fin = (n.get("displayFinancialStatus") or "").upper()
        # COD heuristic: a cash-on-delivery / manual gateway that is still PENDING at creation
        is_cod = any(k in " ".join(gateways) for k in ("cash", "cod", "наложен", "manual")) \
            or fin in ("PENDING", "UNPAID")
        items = [{"title": li["node"]["title"], "qty": li["node"]["quantity"],
                  "sku": (li["node"].get("variant") or {}).get("sku") or li["node"].get("sku"),
                  "barcode": (li["node"].get("variant") or {}).get("barcode")}
                 for li in (n.get("lineItems") or {}).get("edges", [])]
        out.append({
            "id": n["id"], "name": n["name"], "created": n["createdAt"],
            "shopify_fulfillment": n.get("displayFulfillmentStatus"),
            "financial": fin, "total": float(total),
            "cod": is_cod, "cod_amount": float(total) if is_cod else 0.0,
            "customer": (cust.get("firstName", "") + " " + cust.get("lastName", "")).strip()
                        or addr.get("name") or "",
            "phone": addr.get("phone") or "",
            "city": addr.get("city") or "", "zip": addr.get("zip") or "",
            "address": " ".join(x for x in [addr.get("address1"), addr.get("address2")] if x),
            "items": items, "note": n.get("note") or "",
        })
    return out


def state_map():
    con = db.connect(); _ensure(con)
    try:
        rows = con.execute("SELECT order_id, order_name, status, courier, tracking, "
                           "cod_amount, label_ref, updated_ts FROM fulfillment_state").fetchall()
    finally:
        con.close()
    return {r[0]: {"order_name": r[1], "status": r[2], "courier": r[3], "tracking": r[4],
                   "cod_amount": r[5], "label_ref": r[6], "updated_ts": r[7]} for r in rows}


# the only columns set_state may write. Column NAMES are f-string-interpolated into SQL below
# (values are always parameterized), so this allow-list is what keeps a future caller passing a
# user-controlled kwarg key from turning into SQL injection.
_SETTABLE = {"order_name", "status", "courier", "tracking", "cod_amount", "label_ref", "updated_ts"}


def set_state(order_id, order_name, **fields):
    bad = set(fields) - _SETTABLE
    if bad:
        raise ValueError(f"set_state: недопустими полета {sorted(bad)}")
    con = db.connect(); _ensure(con)
    try:
        cur = con.execute("SELECT order_id FROM fulfillment_state WHERE order_id=?", (order_id,)).fetchone()
        fields["updated_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if cur:
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE fulfillment_state SET {sets} WHERE order_id=?",
                        (*fields.values(), order_id))
        else:
            cols = ["order_id", "order_name"] + list(fields)
            con.execute(f"INSERT INTO fulfillment_state({','.join(cols)}) "
                        f"VALUES({','.join('?'*len(cols))})",
                        (order_id, order_name, *fields.values()))
        con.commit()
    finally:
        con.close()


def status_of(order_id, smap=None):
    smap = smap if smap is not None else state_map()
    return (smap.get(order_id) or {}).get("status") or "new"
