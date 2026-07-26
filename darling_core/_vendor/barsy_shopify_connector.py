# -*- coding: utf-8 -*-
"""Bidirectional Barsy ⇄ Shopify connector LOGIC (owner 2026-07-20).

Reusable translation functions for the webhook service. The HTTP layer (receiver endpoints,
signature verification, webhook registration) lives in the admin-panel FastAPI backend — see
`admin-panel/HANDOFF_connector.md`. Neither Barsy nor Shopify talks to the other; this connector
is the piece in the middle.

Every write function defaults to do_write=False (returns a plan, writes nothing). Writes that
mutate live sales/stock (`Accounts_place`, storno, inventory) are marked VERIFY-LIVE — finalize
them against a Barsy test object before going live.

Fiscal model: Barsy IS the fiscal point (owner) → closing a sale fiscalises + draws down stock;
a service POS with a fiscal device must be configured for order reception.

Barsy webhook events are COARSE (per entity): `articles`(1), `articles_availability`(9),
`categories`(6), `accounts`(2), `clientorders`(3), `clients`(4). One handler per event routes by
payload. Loop prevention: `_echo(code, content)` — after one round-trip the values match, so a
change stops propagating instead of ping-ponging.
"""
import hashlib
import json
import time
from pathlib import Path

import barsy_api as b
import barsy_push as bp
import barsy_shopify_sync as sync
import shopify_api as s

HERE = Path(__file__).parent
ORDERS_STATE = HERE / "shopify_orders_synced.jsonl"
ECHO_STORE = HERE / ".connector_echo.json"
DEPOT_ID = 1
POS_ID = 1
_PAYMETHOD = {"id": None}
_LOCATION = {"id": None}

# ============================================================ helpers
def _catalog():
    return bp._catalog()                                   # {code: article}


def _supplier(code):
    import db
    return str(((db.get_record(code) or {}).get("fields") or {}).get("Доставчик") or "")


def _is_mira_n(code):
    """Mira-N products stay BUYABLE at 0 stock (owner) — we order them from the supplier on demand.
    Identified by supplier name or the mira-n.net image host."""
    rec = None
    try:
        import db
        rec = db.get_record(code) or {}
    except Exception:
        rec = {}
    f = rec.get("fields") or {}
    return ("мира" in str(f.get("Доставчик") or "").lower()
            or "mira-n.net" in str(f.get("Снимки") or "").lower())


def _paymethod_id():
    if _PAYMETHOD["id"] is None:
        try:
            pms = b.call("Paymentmethods_getlist", {"filters": {}}) or []
            pick = next((p for p in pms if any(k in str(p.get("name", "")).lower()
                        for k in ("карт", "card", "онлайн", "online"))), None)
            _PAYMETHOD["id"] = (pick or (pms[0] if pms else {})).get("paymethod_id") or 1
        except Exception:
            _PAYMETHOD["id"] = 1
    return _PAYMETHOD["id"]


def _shopify_location():
    if _LOCATION["id"] is None:
        d = s.gql("{ locations(first:1){ nodes{ id } } }")
        _LOCATION["id"] = d["locations"]["nodes"][0]["id"]
    return _LOCATION["id"]


def _echo(code, content):
    """True => this change is an echo of what we last synced (or a no-op) → skip. Breaks loops."""
    h = hashlib.sha1(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    try:
        store = json.loads(ECHO_STORE.read_text(encoding="utf-8")) if ECHO_STORE.exists() else {}
    except Exception:
        store = {}
    if store.get(str(code)) == h:
        return True
    store[str(code)] = h
    ECHO_STORE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return False


def mark_pushed(code):
    """Call after WE write `code` to Barsy (from approved_pipeline) so the resulting Barsy webhook
    is recognised as our echo. Records the current name/price fingerprint."""
    art = _catalog().get(str(code)) or {}
    _echo(code, {"n": art.get("article_name"), "p": art.get("actual_price")})


def _seen_order(oid):
    if not ORDERS_STATE.exists():
        return None
    for l in ORDERS_STATE.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(l)
            if str(d.get("order_id")) == str(oid):
                return d
        except Exception:
            pass
    return None


def _record_order(oid, account_id, n, kind="sale", key=None):
    with ORDERS_STATE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"order_id": str(oid), "account_id": account_id, "rows": n, "kind": kind,
                            "key": key, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n")


def _seen_key(key):
    """True if an action with this idempotency key was already recorded (dedupes repeat webhooks —
    a duplicate cancel/refund/fulfilment delivery must not double-storno or re-write)."""
    if not key or not ORDERS_STATE.exists():
        return False
    for l in ORDERS_STATE.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(l).get("key") == key:
                return True
        except Exception:
            pass
    return False


def _find_shopify(code):
    return sync._find_by_sku(code)                         # (product_id, variant_id)


# ============================================================ A. Shopify → Barsy
def shopify_order_to_barsy(order, mode="sale", do_write=False):
    """New/paid Shopify order → Barsy. mode='sale' finalises immediately (fiscalises + draws down
    stock via Accounts_place); mode='clientorder' logs a client-order for staff review first.
    Idempotent by uuid=shopify-<id>. VERIFY-LIVE (writes a sale)."""
    oid = str(order.get("id") or "")
    name = order.get("name") or f"#{oid}"
    if not oid:
        return {"error": "no order id"}
    if _seen_order(oid):
        return {"skipped": "already logged", "order_id": oid}

    cat = _catalog()
    rows, missing = [], []
    for li in (order.get("line_items") or []):
        sku = str(li.get("sku") or "").strip()
        art = cat.get(sku)
        if not art:
            missing.append(sku or "(no sku)")
            continue
        rows.append({"article_id": int(art["article_id"]), "amount": str(li.get("quantity") or 0),
                     "current_price": str(li.get("price") or art.get("actual_price") or "0")})
    cust, ship = order.get("customer") or {}, order.get("shipping_address") or {}
    account = {"uuid": f"shopify-{oid}", "notes": f"Shopify поръчка {name}",
               "description": f"Shopify order {name} ({oid})",
               "contact_name": (f"{cust.get('first_name','')} {cust.get('last_name','')}".strip()
                                or ship.get("name") or ""),
               "phone": cust.get("phone") or ship.get("phone") or "",
               "delivery_address": ", ".join(x for x in [ship.get("address1"), ship.get("city")] if x)}
    plan = {"order_id": oid, "mode": mode, "rows": len(rows), "missing_skus": missing,
            "would_decrement": [(r["article_id"], r["amount"]) for r in rows]}
    if not rows:
        return {**plan, "error": "no line items matched a Barsy article"}
    if missing:
        # Refuse to book a PARTIAL sale: a human must map the unmatched SKUs first. Booking only the
        # matched lines would make the Barsy sale diverge from the Shopify order and drift stock.
        return {**plan, "error": f"unmatched SKUs {missing} — sale NOT booked, needs review",
                "needs_review": True}
    if not do_write:
        return {**plan, "dry_run": True}

    if mode == "clientorder":
        aid = b.call("Clientorders_create", {"client_order": account, "rows": rows})
        _record_order(oid, aid, len(rows), "clientorder")
        return {**plan, "clientorder_id": aid, "written": True}
    # Only record a payment that Shopify actually COLLECTED. A COD order arrives with
    # financial_status='pending' (cash is collected on delivery, reconciled via the panel's COD
    # flow) — booking its total as "collected online" here would double-count the cash in Barsy's
    # ledger. So attach the online payment only for a genuinely paid (prepaid) order.
    paid_online = str(order.get("financial_status") or "").lower() == "paid"
    # fiscal:0 → RECORD the online payment WITHOUT firing the fiscal device. Fiscalisation (the бон)
    # happens on-site at packing via Accounts_close, per the owner's workflow — so booking the sale
    # here must not print a receipt early. (COD sends no payment at all; collected on delivery.)
    payments = ([{"paymethod_id": _paymethod_id(), "actual_paid_sum": str(order.get("total_price") or ""),
                  "transaction_id": oid, "description": "Shopify (collected online)", "fiscal": 0}]
                if paid_online else [])
    aid = b.call("Accounts_place", {"account": account, "orders": rows, "payments": payments})
    _record_order(oid, aid, len(rows), "sale")
    return {**plan, "account_id": aid, "paid_online": paid_online, "written": True}


def shopify_order_cancellation_to_barsy(order, do_write=False):
    """Order cancelled on Shopify → reverse the Barsy sale (storno) → restores stock. VERIFY-LIVE."""
    oid = str(order.get("id") or "")
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"skipped": "no logged Barsy sale for this order", "order_id": oid}
    key = f"storno-{oid}"
    if _seen_key(key):
        return {"skipped": "already stornoed", "order_id": oid}
    plan = {"order_id": oid, "storno_of_account_id": rec["account_id"]}
    if not do_write:
        return {**plan, "dry_run": True}
    # a storno account referencing the original reverses the sale + restores stock
    res = b.call("Accounts_place", {"account": {"uuid": f"shopify-storno-{oid}",
                 "storno_of_account_id": rec["account_id"],
                 "storno_reason": order.get("cancel_reason") or "Shopify cancellation",
                 "notes": f"Storno на Shopify поръчка {rec.get('order_id')}"}, "orders": [], "payments": []})
    _record_order(oid, res, 0, "storno", key=key)
    return {**plan, "storno_account_id": res, "written": True}


def shopify_refund_to_barsy(refund, do_write=False):
    """Refund (full/partial) on Shopify → partial storno + restock of the refunded lines. VERIFY-LIVE."""
    oid = str(refund.get("order_id") or "")
    rec = _seen_order(oid)
    if not rec:
        return {"skipped": "no logged sale", "order_id": oid}
    key = f"refund-{refund.get('id')}"                      # dedupe per refund (an order can have several)
    if _seen_key(key):
        return {"skipped": "already refunded", "order_id": oid, "refund_id": refund.get("id")}
    cat = _catalog()
    rows = []
    for ri in (refund.get("refund_line_items") or []):
        li = ri.get("line_item") or {}
        art = cat.get(str(li.get("sku") or ""))
        if art:
            rows.append({"article_id": int(art["article_id"]), "amount": str(ri.get("quantity") or 0)})
    plan = {"order_id": oid, "refund_rows": rows}
    if not do_write:
        return {**plan, "dry_run": True}
    res = b.call("Accounts_place", {"account": {"uuid": f"shopify-refund-{refund.get('id')}",
                 "storno_of_account_id": rec["account_id"], "storno_reason": "Shopify refund"},
                 "orders": rows, "payments": []})
    _record_order(oid, res, len(rows), "refund", key=key)
    return {**plan, "storno_account_id": res, "written": True}


def shopify_fulfillment_to_barsy(order, do_write=False):
    """Order fulfilled/shipped on Shopify → write tracking number onto the Barsy account. VERIFY-LIVE."""
    oid = str(order.get("id") or "")
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"skipped": "no logged sale", "order_id": oid}
    ful = (order.get("fulfillments") or [{}])[0]
    track = ful.get("tracking_number") or order.get("tracking_number") or ""
    key = f"track-{oid}-{track}"                            # dedupe per (order, waybill)
    if track and _seen_key(key):
        return {"skipped": "tracking already written", "order_id": oid, "tracking_number": track}
    plan = {"order_id": oid, "account_id": rec["account_id"], "tracking_number": track}
    if not do_write:
        return {**plan, "dry_run": True}
    # continue the existing account by id and set its tracking number
    b.call("Accounts_place", {"account_id": rec["account_id"],
                              "account": {"tracking_number": track}, "orders": [], "payments": []})
    _record_order(oid, rec["account_id"], 0, "fulfillment", key=key)
    return {**plan, "written": True}


def shopify_customer_to_barsy(customer, do_write=False):
    """New/updated Shopify customer → Barsy client (Clients_createsmart dedupes by phone/email)."""
    cl = {"client_name": f"{customer.get('first_name','')} {customer.get('last_name','')}".strip()
          or customer.get("email") or "",
          "phone": customer.get("phone") or "", "email": customer.get("email") or "",
          "export_id": f"shopify-{customer.get('id')}"}
    if not do_write:
        return {"dry_run": True, "client": cl}
    cid = b.call("Clients_createsmart", {"client": cl})
    return {"client_id": cid, "written": True}


def shopify_product_edit_to_barsy(product, do_write=False, enabled=False):
    """OPT-IN (off by default — owner doesn't edit in Shopify). If a product is edited IN Shopify,
    mirror title/price to Barsy. Echo-guarded so it can't loop with Direction B."""
    if not enabled:
        return {"skipped": "shopify->barsy product edit disabled (opt-in)"}
    var = (product.get("variants") or [{}])[0]
    code = str(var.get("sku") or "")
    art = _catalog().get(code)
    if not art:
        return {"skipped": "not in Barsy", "code": code}
    content = {"n": product.get("title"), "p": var.get("price")}
    if _echo(code, content):
        return {"skipped": "echo/no-op (loop-guard)", "code": code}
    plan = {"code": code, "article_id": art["article_id"], **content}
    if not do_write:
        return {**plan, "dry_run": True}
    b.call("Articles_update", {"article_id": int(art["article_id"]),
           "article": {"article_name": product.get("title"), "actual_price": str(var.get("price") or "")}})
    return {**plan, "written": True}


# ---- COD lifecycle (driven by the panel's наложен-платеж flow, not a Shopify webhook) ----
def cod_refused_to_barsy(oid, reason="COD отказан (RTO)", do_write=False):
    """A REFUSED COD delivery (RTO) → storno the Barsy sale so the returned goods go back into stock.
    Mechanically identical to a Shopify cancellation (idempotent, keyed). The panel calls this when it
    records an order outcome as 'refused'."""
    return shopify_order_cancellation_to_barsy({"id": str(oid), "cancel_reason": reason}, do_write=do_write)


def cod_payment_to_barsy(oid, amount, paymethod_id=5, waybill=None, do_write=False):
    """A SETTLED COD collection → record the received payment on the Barsy sale account. fiscal:0 →
    NO fiscal device is fired (the бон was already printed on-site at packing); this only books the
    money against the (already-fiscalised) sale. paymethod_id 5 = По банка (BG COD couriers bank the
    batch); use 1 for cash-in-hand. Idempotent per order via the connector's action log."""
    oid = str(oid)
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"skipped": "no logged Barsy sale for this order", "order_id": oid}
    key = f"codpay-{oid}"
    if _seen_key(key):
        return {"skipped": "COD payment already posted", "order_id": oid}
    pay = {"paymethod_id": paymethod_id, "actual_paid_sum": str(amount), "fiscal": 0,
           "description": f"COD събран (куриер){f' · {waybill}' if waybill else ''}"}
    plan = {"order_id": oid, "account_id": rec["account_id"], "amount": str(amount), "fiscal": 0}
    if not do_write:
        return {**plan, "dry_run": True}
    b.call("Accounts_place", {"account_id": rec["account_id"], "orders": [], "payments": [pay]})
    _record_order(oid, rec["account_id"], 0, "cod_payment", key=key)
    return {**plan, "written": True}


# ============================================================ B. Barsy → Shopify
def barsy_new_product_to_shopify(code, do_write=False):
    """New Barsy article → Shopify product. If the code is in our enriched DB, use the rich
    sync_code path; otherwise build a minimal product from the Barsy article."""
    code = str(code)
    if _find_shopify(code)[0]:
        return {"skipped": "already on Shopify", "code": code}
    import db
    if db.get_record(code):
        return {"delegated": "sync.sync_code", **(sync.sync_code(code, do_write=do_write) if do_write
                else {"dry_run": True})}
    art = _catalog().get(code)
    if not art:
        return {"skipped": "not in Barsy", "code": code}
    pin = {"title": art.get("article_name") or code, "status": "DRAFT"}
    plan = {"code": code, "title": pin["title"], "price": art.get("actual_price")}
    if not do_write:
        return {**plan, "dry_run": True}
    d = s.gql("""mutation($p:ProductCreateInput!){ productCreate(product:$p){
                 product{ id variants(first:1){ nodes{ id } } } userErrors{ message } } }""", {"p": pin})
    pid = d["productCreate"]["product"]["id"]
    vid = d["productCreate"]["product"]["variants"]["nodes"][0]["id"]
    s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid,
             variants:$v){ userErrors{ message } } }""",
          {"pid": pid, "v": [{"id": vid, "price": str(art.get("actual_price") or "0"),
                              "inventoryItem": {"sku": code, "tracked": False}}]})
    return {**plan, "product_id": pid, "written": True}


def barsy_article_to_shopify(code, do_write=False):
    """Barsy master-data change (name/price) → update the Shopify product. Echo-guarded."""
    code = str(code)
    art = _catalog().get(code)
    if not art:
        return {"skipped": "not in Barsy", "code": code}
    content = {"n": art.get("article_name"), "p": art.get("actual_price")}
    if _echo(code, content):
        return {"skipped": "echo/no-op (loop-guard)", "code": code}
    pid, vid = _find_shopify(code)
    if not pid:
        return {"skipped": "not on Shopify yet", "code": code}
    title, price = str(art.get("article_name") or "").strip(), str(art.get("actual_price") or "").strip()
    plan = {"code": code, "product_id": pid, "title": title, "price": price}
    if not do_write:
        return {**plan, "dry_run": True}
    if title:
        s.gql("mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ message } } }",
              {"p": {"id": pid, "title": title}})
    if price and vid:
        s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(
                 productId:$pid, variants:$v){ userErrors{ message } } }""",
              {"pid": pid, "v": [{"id": vid, "price": price}]})
    return {**plan, "written": True}


def barsy_quantity_change_to_shopify(code, qty=None, do_write=False):
    """Barsy stock change (articles_availability) → Shopify inventory. Mira-N products stay
    BUYABLE at 0 (inventoryPolicy CONTINUE); all others DENY (sell out at 0). Enables tracking so
    live stock shows. VERIFY-LIVE (Shopify inventory API + location). qty auto-read from Barsy if None."""
    code = str(code)
    art = _catalog().get(code)
    if not art:
        return {"skipped": "not in Barsy", "code": code}
    # Read stock AND the buyable-at-0 policy from BARSY (the system of record). sale_below_zero
    # YES = oversellable → Shopify CONTINUE; NO/DEF = DENY (sold out at 0). Mira-N products carry
    # sale_below_zero=YES in Barsy (set at push time), so this is fully Barsy-driven — no heuristic.
    sbz = str(art.get("sale_below_zero") or "").upper()
    try:
        g = b.call("Articles_get", {"article_id": int(art["article_id"]),
                                    "extra_properties": {"store_amount": "1"}})
        if qty is None:
            qty = g.get("store_amount") or 0
        sbz = str(g.get("sale_below_zero") or sbz).upper()
    except Exception:
        qty = qty if qty is not None else 0
    policy = "CONTINUE" if sbz == "YES" else "DENY"
    pid, vid = _find_shopify(code)
    if not pid:
        return {"skipped": "not on Shopify yet", "code": code}
    plan = {"code": code, "qty": qty, "sale_below_zero": sbz, "inventory_policy": policy}
    if not do_write:
        return {**plan, "dry_run": True}
    # 1) inventoryPolicy on the variant; 2) enable tracking; 3) set on-hand at the primary location
    inv = s.gql("""query($id:ID!){ productVariant(id:$id){ inventoryItem{ id } } }""", {"id": vid})
    inv_item = inv["productVariant"]["inventoryItem"]["id"]
    s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid,
             variants:$v){ userErrors{ message } } }""",
          {"pid": pid, "v": [{"id": vid, "inventoryPolicy": policy,
                              "inventoryItem": {"tracked": True}}]})
    s.gql("""mutation($id:ID!){ inventoryItemUpdate(id:$id, input:{tracked:true}){ userErrors{ message } } }""",
          {"id": inv_item})
    s.gql("""mutation($in:InventorySetOnHandQuantitiesInput!){ inventorySetOnHandQuantities(input:$in){
             userErrors{ message } } }""",
          {"in": {"reason": "correction", "setQuantities": [
              {"inventoryItemId": inv_item, "locationId": _shopify_location(), "quantity": int(float(qty or 0))}]}})
    return {**plan, "written": True}


def barsy_deleted_product_to_shopify(code, do_write=False):
    """Barsy article delete_flag=1 → ARCHIVE the Shopify product (safer than delete; keeps order
    history). Change to productDelete if you truly want it gone."""
    code = str(code)
    pid, _ = _find_shopify(code)
    if not pid:
        return {"skipped": "not on Shopify", "code": code}
    plan = {"code": code, "product_id": pid, "action": "archive"}
    if not do_write:
        return {**plan, "dry_run": True}
    s.gql("""mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ message } } }""",
          {"p": {"id": pid, "status": "ARCHIVED"}})
    return {**plan, "written": True}


def barsy_category_change_to_shopify(cat_id, do_write=False):
    """Barsy category change → Shopify. LIMITED: Shopify's product category is the fixed Shopify
    taxonomy (set from our leaf_gid), and collections here are auto-built from tags — a Barsy
    category RENAME doesn't move Shopify taxonomy. Real use: re-tag affected products so collection
    membership follows. Returns the affected codes for the panel to re-tag; wire per your collection
    strategy. VERIFY-LIVE."""
    try:
        arts = b.call("Articles_getlist", {"filters": {"cat_id": str(cat_id)}}) or []
    except Exception as e:
        return {"error": str(e)[:80]}
    codes = [str(a.get("code")) for a in arts if a.get("code")]
    return {"cat_id": cat_id, "affected_codes": codes, "note": "re-tag these per collection strategy",
            "dry_run": not do_write}


def barsy_image_change_to_shopify(code, do_write=False):
    """Barsy/processed image changed → replace the Shopify media with the current processed cutout
    (staged upload). Deletes existing media first so it doesn't accumulate. VERIFY-LIVE."""
    code = str(code)
    pid, _ = _find_shopify(code)
    if not pid:
        return {"skipped": "not on Shopify", "code": code}
    files = bp._processed_images(code, max_images=8)
    plan = {"code": code, "product_id": pid, "processed_images": [p.name for p in files]}
    if not files:
        return {**plan, "skipped": "no processed image"}
    if not do_write:
        return {**plan, "dry_run": True}
    ex = s.gql("query($id:ID!){ product(id:$id){ media(first:20){ nodes{ id } } } }", {"id": pid})
    mids = [m["id"] for m in ex["product"]["media"]["nodes"]]
    if mids:
        s.gql("""mutation($pid:ID!,$ids:[ID!]!){ productDeleteMedia(productId:$pid, mediaIds:$ids){
                 deletedMediaIds userErrors{ message } } }""", {"pid": pid, "ids": mids})
    media = [{"originalSource": sync._staged_upload(p.name, p.read_bytes()), "mediaContentType": "IMAGE"}
             for p in files]
    s.gql("""mutation($pid:ID!,$m:[CreateMediaInput!]!){ productCreateMedia(productId:$pid, media:$m){
             mediaUserErrors{ message } } }""", {"pid": pid, "m": media})
    return {**plan, "written": True}


# ============================================================ infrastructure
def register_all_webhooks(receiver_base):
    """One-time setup. Subscribes the Barsy → connector webhooks and returns the Shopify topics the
    panel must register (Shopify webhooks are created on Shopify's side via webhookSubscriptionCreate).
    receiver_base e.g. https://panel.example.com/webhooks"""
    barsy = {}
    for ev, path in [("articles", "barsy/article"), ("articles_availability", "barsy/stock"),
                     ("categories", "barsy/category"), ("accounts", "barsy/order")]:
        try:
            barsy[ev] = subscribe_barsy_webhook(ev, f"{receiver_base}/{path}")
        except Exception as e:
            barsy[ev] = {"error": str(e)[:80]}
    shopify_topics = ["orders/paid", "orders/cancelled", "refunds/create",
                      "fulfillments/create", "customers/create", "customers/update"]
    return {"barsy_subscribed": barsy, "shopify_topics_to_register": shopify_topics,
            "receiver_base": receiver_base}


def reconcile(limit=None, do_write=False):
    """Safety-net sweep: webhooks DO get missed, so periodically diff Barsy vs Shopify (price now;
    extend to stock/status) and fix drift. Run nightly. Returns the drift list; do_write applies fixes."""
    cat = _catalog()
    drift = []
    for code, art in list(cat.items())[:(limit or len(cat))]:
        pid, vid = _find_shopify(code)
        if not pid:
            continue
        bp_price = str(art.get("actual_price") or "").strip()
        try:
            sp = s.gql("query($id:ID!){ productVariant(id:$id){ price } }", {"id": vid})
            sh_price = str(sp["productVariant"]["price"] or "").strip()
        except Exception:
            continue
        if bp_price and bp_price != sh_price:
            drift.append({"code": code, "barsy": bp_price, "shopify": sh_price})
            if do_write:
                barsy_article_to_shopify(code, do_write=True)
    return {"checked": min(limit or len(cat), len(cat)), "drift": drift, "fixed": do_write}


# ---- Barsy webhook registration ----
BARSY_EVENTS = {"articles": 1, "articles_availability": 9, "categories": 6,
                "accounts": 2, "clientorders": 3, "clients": 4}


def subscribe_barsy_webhook(event_name, receiver_url):
    et_id = BARSY_EVENTS.get(event_name)
    if not et_id:
        return {"error": f"unknown event {event_name}", "known": list(BARSY_EVENTS)}
    return b.call("Webhooks_subscribe", {"event_type_id": et_id, "url": receiver_url})
