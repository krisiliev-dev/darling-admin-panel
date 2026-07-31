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
import re
import threading
import time
import uuid as _uuid
from pathlib import Path


def _acc_uuid(seed):
    """Barsy `account.uuid` must be a valid UUID; the raw "shopify-<id>" string is rejected
    ("Некоректна дата … id:UUID"). Derive a UUID deterministically from a stable seed so
    idempotency still holds AND the value passes Barsy's UUID validation."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, str(seed)))

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
# COD model (owner, 2026-07-28): online orders carry NO client (Barsy's built-in "Анонимен") — a Client
# means a B2B/tab-holder, not a random online buyer. Barsy forbids an anonymous account from OWING
# (hard rule "Анонимен клиент не може да задлъжнява"), so a COD sale is finalised as PAID at packing:
# closed with a payment under the dedicated "Наложен платеж" method (id 6, fiscal — prints the бон,
# is_cash, anonymous-allowed) and the fiscal бон fires then. Uncollected orders are caught later by
# the panel's courier-remittance reconciliation → storno "Връщане/рекламация". Booking COD under its
# OWN method (not В брой) keeps it isolatable in reports/reconciliation. The account total to pay is
# read from Clientorders_get(cid).total_sum at finalise. (supersedes the earlier credit_limit approach)
COD_PAYMETHOD_ID = 6
# Barsy's built-in anonymous client. Online orders attach to this (not a per-buyer Client) — a Barsy
# "Client" is a B2B/tab-holder, and an anonymous account closes PAID (it can't owe), which is the COD model.
_ANON_CLIENT_ID = 1
# Barsy code of the "Доставка" SERVICE article (is_service=1 → no stock movement; tax_id 2 = 20%, matching
# the goods, per BG rules where delivery follows the main supply). Shopify `shipping_lines` are booked
# against it so the account total equals what the courier actually collects. (owner 2026-07-28)
SHIPPING_CODE = "DELIVERY"
_LOCATION = {"id": None}

# ============================================================ helpers
def _catalog():
    return bp._catalog()                                   # {code: article}


def _price(art):
    """Price to publish to the storefront = the article's ONLINE price: `current_price` (the
    "address"/online delivery channel, which reflects any active Barsy Pricelists rule scoped to
    online) with a fallback to the base `actual_price` when no online rule applies. Prices always
    originate in Barsy/Microinvest and are never AI-touched — this only selects the online channel's
    price. With no online rule set the two are identical, so this is a no-op until channel pricing
    is configured. NOTE: incoming Shopify orders book at the price the customer actually paid (the
    order line), NOT this — do not use `_price` on the Shopify→Barsy sale path."""
    cp = art.get("current_price")
    return str(cp if cp not in (None, "") else (art.get("actual_price") or ""))


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
    # Fingerprint MUST match the one barsy_article_to_shopify computes (same keys, same price accessor) or
    # the SHA1s never line up and the echo guard silently never fires. (audit fix 2026-07-28)
    _echo(code, {"n": _display_name(art), "p": _price(art), "st": sync.shopify_status(art)})


def _seen_order(oid):
    """Return the LATEST recorded state for an order (so a finalise-appended kind='sale' overrides the
    original kind='clientorder' — the cancel-branch keys on this)."""
    latest = None
    for d in _state_records():
        if str(d.get("order_id")) == str(oid):
            latest = d
    return latest


def record_finalised(order_id, account_id, rows=0):
    """HOOK for the panel's review→finalise endpoint: call AFTER Accounts_createfromclientorder so the
    connector flips this order from kind='clientorder' to kind='sale' (+ the new sale account_id). Then a
    later cancel/refund correctly hits the storno branch instead of voiding a clientorder that's already
    been finalised into a sale."""
    _record_order(str(order_id), account_id, rows, "sale")
    return {"order_id": str(order_id), "account_id": account_id, "kind": "sale"}


_STATE_CACHE = {"sig": None, "records": []}


def _state_records():
    """Parsed ORDERS_STATE records, cached on (mtime, size). Every lookup used to re-read and re-parse the
    whole append-only log — fine at test volume, but it's read on every webhook/dedup/finalise check and the
    file only grows. The signature makes the cache self-invalidating: any append changes size/mtime, and
    _record_order busts it explicitly, so a stale read is not possible. (audit perf fix 2026-07-28)"""
    try:
        st = ORDERS_STATE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _STATE_CACHE.update(sig=None, records=[])
        return []
    if _STATE_CACHE["sig"] != sig:
        recs = []
        for line in ORDERS_STATE.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
        _STATE_CACHE.update(sig=sig, records=recs)
    return _STATE_CACHE["records"]


def _record_order(oid, account_id, n, kind="sale", key=None, **extra):
    """Append an order-state record. `account_id` must ALWAYS stay the SALE/clientorder account — a storno
    records its own id under `storno_account_id` via **extra, never overwriting account_id (else the next
    _seen_order lookup would aim refunds/fulfilment at the storno document). (audit fix 2026-07-28)"""
    with ORDERS_STATE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"order_id": str(oid), "account_id": account_id, "rows": n, "kind": kind,
                            "key": key, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra},
                           ensure_ascii=False) + "\n")
    _STATE_CACHE["sig"] = None                             # bust the read cache immediately after append


def _barsy_account_for_clientorder(cid):
    """Ask BARSY (the system of record) whether client_order <cid> already has a sale account — the
    durable idempotency source that survives a container recreate wiping ORDERS_STATE.jsonl. Barsy's own
    rule ('по клиентска заявка N вече има създадена сметка, покриваща сумата') is what really blocks a
    double-decrement, so we mirror it: return the existing account_id (or None). A storno'd account still
    counts — Barsy considers the clientorder consumed and refuses a re-finalise regardless of reversal."""
    cid = str(cid)
    try:
        for a in (b.call("Accounts_getlist", {"filters": {}}) or []):
            ids = str(a.get("client_order_ids") or "")
            # client_order_ids is a comma-joined string ("3" or "3,5"); match the exact token
            if cid in [t.strip() for t in ids.split(",") if t.strip()]:
                return a.get("account_id")
    except Exception:
        pass
    return None


def _invoice_for_order(oid):
    """Re-read a B2B invoice request straight from Shopify (customAttributes = the order's note_attributes).
    Fallback for finalise when the intake-captured invoice data isn't in ORDERS_STATE — e.g. the log was
    rebuilt by webhooks after a container recreate. Returns InvoiceMakeData or None; never raises."""
    try:
        d = s.gql("""query($id:ID!){ order(id:$id){ customAttributes{ key value }
                     customer{ firstName lastName } } }""", {"id": f"gid://shopify/Order/{oid}"})
        o = d.get("order") or {}
        attrs = [{"name": a.get("key"), "value": a.get("value")} for a in (o.get("customAttributes") or [])]
        cust = o.get("customer") or {}
        name = f"{cust.get('firstName','') or ''} {cust.get('lastName','') or ''}".strip()
        return _invoice_data({"note_attributes": attrs}, {"contact_name": name})
    except Exception:
        return None


_SPEDITORS = {"map": None}


def _speditor_key(s):
    """Normalise a courier name for matching: lowercase, strip everything that isn't a letter/digit.
    The storefront sends machine keys ("boxnow"), Barsy stores display names ("BOX NOW") — an exact
    lowercase compare misses on the space alone, and the failure is SILENT (no speditor_id → the waybill
    is stamped with no courier attached). (fixed 2026-07-31)"""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _speditor_id(name):
    """Courier NAME → Barsy speditor_id (AccountInputData wants the id). Space/punctuation- and
    case-insensitive, and tolerates the Speedy/Спиди spelling split (Barsy registers it latin as
    "Speedy" while our couriers/labels say "Спиди"). Returns None if unknown — the caller omits the
    field rather than erroring, so callers that CARE must check for None explicitly."""
    if _SPEDITORS["map"] is None:
        try:
            m = {_speditor_key(x.get("speditor_name")): x.get("speditor_id")
                 for x in (b.call("Speditors_getlist", {"filters": {}}) or [])}
        except Exception:
            m = {}
        # A failed or empty fetch must NOT be cached: caching it would poison every later lookup for
        # the life of the process, so one transient Barsy error would silently strip the courier off
        # every subsequent waybill. Return None (same as "unknown courier") but retry next call.
        if not m:
            return None
        # cross-script aliases: our keys/labels vs Barsy's stored spelling
        for alias, canon in (("спиди", "speedy"), ("speedy", "спиди"),
                             ("econt", "еконт"), ("еконт", "econt")):
            if canon in m and alias not in m:
                m[alias] = m[canon]
        _SPEDITORS["map"] = m
    return _SPEDITORS["map"].get(_speditor_key(name))


def _display_name(art):
    """The STOREFRONT title for a Barsy article. Owner 2026-07-28: prefer `article_name_public` (Barsy's
    dedicated customer-facing name) when set, else the internal/till `article_name`. Currently every
    article_name_public is NULL → this transparently falls back to article_name (no behaviour change) until
    enrichment starts populating the public name."""
    art = art or {}
    return (str(art.get("article_name_public") or "").strip()
            or str(art.get("article_name") or "").strip())


_OP_LOCKS = {}
_OP_LOCKS_GUARD = threading.Lock()


def _op_lock(key):
    """Per-key in-process lock. Shopify fires orders/create AND orders/updated for the same order
    ~simultaneously; the receiver dispatches BOTH to concurrent run_in_threadpool tasks, and without
    this they BOTH pass the _seen_* dedup (read the state file before either writes it) and double-book.
    Holding this lock across the check-and-book serialises same-order tasks; the persistent state file
    still covers cross-process retries. (Assumes a single uvicorn worker — the deploy runs one; a
    multi-worker setup would additionally need a file lock, since threading locks don't span processes.)"""
    with _OP_LOCKS_GUARD:
        lk = _OP_LOCKS.get(key)
        if lk is None:
            lk = _OP_LOCKS[key] = threading.Lock()
        return lk


def _seen_key(key):
    """True if an action with this idempotency key was already recorded (dedupes repeat webhooks —
    a duplicate cancel/refund/fulfilment delivery must not double-storno or re-write)."""
    if not key:
        return False
    for d in _state_records():
        try:
            if d.get("key") == key:
                return True
        except Exception:
            pass
    return False


def _find_shopify(code):
    return sync._find_by_sku(code)                         # (product_id, variant_id)


def _note_attrs(order):
    """Shopify order note_attributes ([{name,value}]) → dict. The storefront's cart 'Фактура за фирма'
    section writes darling_invoice_* here (Basic plan can't add structured checkout fields)."""
    return {str(a.get("name")): a.get("value")
            for a in (order.get("note_attributes") or []) if a.get("name")}


def _invoice_data(order, account):
    """Build Barsy Accounts_place invoice_data (InvoiceMakeData) from the storefront's darling_invoice_*
    note-attributes when the buyer ticked 'фактура за фирма'. None otherwise. Barsy issues + (given the
    account email) emails the VAT invoice; this is the фактура, NOT the fiscal бон (device-printed)."""
    na = _note_attrs(order)
    if str(na.get("darling_invoice") or "").lower() != "true":
        return None
    return {
        "gen_mode": 1,                                     # 1 = detailed (all articles)
        "receiver_company_name": na.get("darling_invoice_company") or "",
        "receiver_identity_num": na.get("darling_invoice_eik") or "",     # ЕИК
        "receiver_vat_num": na.get("darling_invoice_vat") or "",          # ДДС № (optional)
        "receiver_mol": na.get("darling_invoice_mol") or "",
        "receiver_name": na.get("darling_invoice_mol") or account.get("contact_name") or "",
        "receiver_town": na.get("darling_invoice_city") or "",
        "receiver_address": na.get("darling_invoice_address") or "",
    }


# --- sale finalisation flags (Accounts_place) ---------------------------------------------------
# flag_close_account=1 makes Accounts_place do the FULL lifecycle in one call: open + order + payment
# + CLOSE, and CLOSE is what writes off (decrements) stock. Payments are only accepted at close, so
# they ride the same call. Barsy recommends this single-call finalise for online-shop integrations.
_SALE_FLAG_CLOSE = 1        # finalise on order receipt (draws down stock). LAUNCH fiscal-timing may
                            # move the close to the packing step — make this env-driven then.
_SALE_PRINT_RECEIPT = 0     # TEST: no fiscal receipt. On the non-fiscal test POS (no device) closing
                            # fires no бон regardless; LAUNCH (Barsy = fiscal point) wants 1 → env-drive.
_SALE_PRINT_SHIPMENT = 0    # never print a courier label from the sale path (that's the courier module).


def _ensure_client(order):
    """DEPRECATED under the anonymous COD model (owner 2026-07-28): online orders no longer get a per-buyer
    Barsy Client — they attach to `_ANON_CLIENT_ID` and finalise as PAID via the "Наложен платеж" method.
    Kept as a no-op hook in case B2B online orders later need a real Client. Returns None (→ anonymous)."""
    return None


# ============================================================ A. Shopify → Barsy
def shopify_order_to_barsy(order, mode="sale", do_write=False):
    """New/paid Shopify order → Barsy. mode='sale' finalises immediately (fiscalises + draws down
    stock via Accounts_place); mode='clientorder' logs a client-order for staff review first.
    Idempotent by uuid=shopify-<id>. VERIFY-LIVE (writes a sale)."""
    oid = str(order.get("id") or "")
    name = order.get("name") or f"#{oid}"
    if not oid:
        return {"status": "error", "reason": "no_order_id", "error": "no order id"}
    if _seen_order(oid):                                    # fast-path dedup (re-checked inside the lock)
        return {"status": "duplicate", "skipped": f"already seen {name}", "order_id": oid, "order_ref": name}

    # PLATFORM INTAKE GUARDS. `issues` is attached by the storefront adapter (woo_orders.normalise) for
    # faults visible on ITS side and invisible here: a blank/unvalidated courier office id (no waybill
    # can be produced at all), or a fabricated shipping rate charged while the pricing service was down.
    # The latter is why this cannot be folded into `total_mismatch` below — a made-up fee is INTERNALLY
    # CONSISTENT, so Woo-total == Barsy-booked still holds while the customer was charged a price no
    # courier ever quoted. External truth needs its own guard. Refuse BEFORE booking: a guard the intake
    # never consults is not a guard, and this one was only ever enforced in the adapter's own tests until
    # now. Refused orders are NOT passed to _record_order, so the order retries cleanly once the
    # storefront corrects the meta rather than being permanently swallowed as a duplicate. (wired 2026-07-31)
    # Any non-empty `issues` blocks. An entry missing its `reason` is MALFORMED, not absent: dropping
    # it would book the order on the strength of a flag we failed to parse — fail-open on the exact
    # class of silent degradation this guard exists to stop. Give it a name and refuse. (2026-07-31)
    _issues = [dict(i) if isinstance(i, dict) else {"detail": str(i)} for i in (order.get("issues") or [])]
    for _i in _issues:
        _i.setdefault("reason", "unnamed_issue")
    if _issues:
        return {**{"status": "needs_review", "reason": _issues[0]["reason"]},
                "order_ref": name, "order_id": oid,
                "detail": {"issues": _issues},
                "error": "; ".join(f"{i['reason']}: {i.get('detail', '')}" for i in _issues)
                         + " — sale NOT booked",
                "needs_review": True}

    cat = _catalog()
    rows, missing = [], []
    for li in (order.get("line_items") or []):
        sku = str(li.get("sku") or "").strip()
        art = cat.get(sku)
        if not art:
            missing.append(sku or "(no sku)")
            continue
        # Carry the Shopify line price (what the customer was actually charged) so Barsy books the
        # correct order value — accounting/COD-reconciliation correctness. The two Barsy methods use
        # DIFFERENT price fields: Accounts_place (sale) reads `original_current_price` (its `current_price`
        # is DEPRECATED/dropped); Clientorders_create reads `current_price` (create.rst). Passing BOTH
        # (each other method drops the field it doesn't use) makes sale AND clientorder book the paid price
        # — else the clientorder silently falls back to the article's base price. (fixed 2026-07-28)
        # DISCOUNTS: `price` on a webhook line item is the ORIGINAL unit price. An automatic/bundle
        # discount does NOT reduce it — it arrives as `total_discount` and/or `discount_allocations[]`.
        # Booking `price` would over-declare the бон and break COD reconciliation by the discount amount
        # (same failure class as the un-booked shipping line). Book what the customer actually pays.
        _qty = float(li.get("quantity") or 0) or 1.0
        _gross_unit = float(li.get("price") or art.get("actual_price") or 0)
        _disc = float(li.get("total_discount") or 0) + sum(
            float(a.get("amount") or 0) for a in (li.get("discount_allocations") or []))
        _net_unit = max(_gross_unit - (_disc / _qty), 0.0)
        _line_price = f"{_net_unit:.2f}"
        # Price field differs PER METHOD and they are mutually exclusive — Clientorders_create hard-errors
        # "Both current_price and original_price are given. You should provide only 1 of them!" (verified
        # live 2026-07-28), so the row is shaped per mode below, not with every field at once.
        rows.append({"article_id": int(art["article_id"]), "amount": str(li.get("quantity") or 0),
                     "_price": _line_price})
    # SHIPPING: the courier collects goods + delivery, so Barsy must book both or the бон under-declares
    # and the COD can never match the remittance. Shopify sends the charge in `shipping_lines` (absent or
    # 0.00 on free-over-threshold orders → nothing is added, so this is threshold-agnostic). Booked against
    # the "Доставка" SERVICE article (is_service=1 ⇒ no stock movement, 20% VAT like the goods).
    _ship_total = sum(float(sl.get("price") or 0) for sl in (order.get("shipping_lines") or []))
    if _ship_total > 0:
        _ship_art = cat.get(SHIPPING_CODE)
        if _ship_art:
            rows.append({"article_id": int(_ship_art["article_id"]), "amount": "1",
                         "_price": f"{_ship_total:.2f}"})
        else:
            # Never silently drop the charge — booking goods-only would under-declare the бон.
            missing.append(f"{SHIPPING_CODE} (доставка {_ship_total:.2f})")

    cust, ship = order.get("customer") or {}, order.get("shipping_address") or {}
    account = {"uuid": _acc_uuid(f"shopify-{oid}"), "notes": f"Shopify поръчка {name}",
               "description": f"Shopify order {name} ({oid})",
               "contact_name": (f"{cust.get('first_name','')} {cust.get('last_name','')}".strip()
                                or ship.get("name") or ""),
               "phone": cust.get("phone") or ship.get("phone") or "",
               "delivery_address": ", ".join(x for x in [ship.get("address1"), ship.get("city")] if x)}
    plan = {"order_id": oid, "order_ref": name, "mode": mode, "rows": len(rows), "missing_skus": missing,
            "would_decrement": [(r["article_id"], r["amount"]) for r in rows]}
    if not rows:
        return {**plan, "status": "refused", "reason": "no_barsy_match",
                "error": "no line items matched a Barsy article", "needs_review": True}
    if missing:
        # Refuse to book a PARTIAL sale: a human must map the unmatched SKUs first. Booking only the
        # matched lines would make the Barsy sale diverge from the Shopify order and drift stock.
        return {**plan, "status": "needs_review", "reason": "unmatched_skus", "detail": {"missing_skus": missing},
                "error": f"unmatched SKUs {missing} — sale NOT booked, needs review", "needs_review": True}
    # TOTALS BACKSTOP: what we're about to book must equal what the customer pays (and what the courier
    # collects on COD). This is deliberately generic — it catches ANY pricing divergence (a discount shape
    # we don't model, a tax quirk, a future Shopify change), not just the ones we've thought of. Both known
    # money bugs so far — un-booked shipping, and pre-discount line prices — would have been caught here.
    _shop_total = float(order.get("total_price") or 0)
    if _shop_total:
        _booked = sum(float(r["_price"]) * float(r["amount"] or 0) for r in rows)
        _diff = _booked - _shop_total
        if abs(_diff) > 0.01:
            # Cheap, DETERMINISTIC cause hint (arithmetic match only — never a guess): if the gap equals
            # the order's shipping or discount total, that component is the likely culprit. Lets the panel
            # tell staff "three of these are one cause", not three unrelated incidents.
            _disc_total = sum(float(x.get("total_discount") or 0) +
                              sum(float(a.get("amount") or 0) for a in (x.get("discount_allocations") or []))
                              for x in (order.get("line_items") or []))
            suspect = "unknown"
            if _ship_total and abs(abs(_diff) - _ship_total) <= 0.01:
                suspect = "shipping"
            elif _disc_total and abs(abs(_diff) - _disc_total) <= 0.01:
                suspect = "discount"
            return {**plan, "status": "needs_review", "reason": "total_mismatch",
                    "detail": {"booked": round(_booked, 2), "shopify_total": _shop_total,
                               "difference": round(_diff, 2), "suspect": suspect},
                    "error": f"Barsy total {_booked:.2f} != Shopify total {_shop_total:.2f} — NOT booked "
                             f"(бон would misdeclare and COD would not reconcile)", "needs_review": True}
    if not do_write:
        return {**plan, "dry_run": True}

    # Serialise concurrent deliveries of the SAME order (Shopify fires create+updated ~together) so they
    # can't both pass the dedup and double-book. The re-check inside the lock is the authoritative one.
    with _op_lock(f"order-{oid}"):
        if _seen_order(oid):
            return {**plan, "status": "duplicate", "skipped": f"already seen {name}"}
        account["client_id"] = _ANON_CLIENT_ID         # anonymous online buyer (COD finalises as PAID)
        if mode == "clientorder":
            # clientorders/create param is `$order` (ClientOrderInputData), not `client_order`. NOTE: `account`
            # is AccountInputData-shaped; ClientOrderInputData differs (e.g. client_tel) — header binds now, but
            # a fuller ClientOrderInputData field mapping is still TODO (flagged).
            # ClientOrderInputRowData: `original_price` is the documented row unit-price ("ако не е
            # подадена, ще бъде ползвана текущата цена"). Sending it alongside current_price is a hard
            # 501, and omitting it entirely made a DISCOUNTED line fall back to the article's base price.
            co_rows = [{"article_id": r["article_id"], "amount": r["amount"],
                        "original_price": r["_price"]} for r in rows]
            # B2B VAT invoice (фактура): flag it on the clientorder so Barsy expects one, and CARRY the
            # buyer's company data forward — finalise issues it via Accounts_createfromclientorder's
            # invoice_data. Stored in our state so the finalise step needs no Shopify round-trip.
            inv = _invoice_data(order, account)
            co = dict(account)
            if inv:
                co["with_invoice"] = 1
                email = (order.get("customer") or {}).get("email") or order.get("email") or ""
                if email:
                    co["email_invoices"] = email          # Barsy emails the фактура to the buyer
            aid = b.call("Clientorders_create", {"order": co, "rows": co_rows})
            _record_order(oid, aid, len(rows), "clientorder", **({"invoice": inv} if inv else {}))
            return {**plan, "status": "ok", "clientorder_id": aid,
                    **({"with_invoice": True} if inv else {}), "written": True}
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
        # flag_close_account=1 → open+order+pay+CLOSE in one call; CLOSE decrements stock. print flags off
        # for the no-fiscal/no-label test scope (and the device-less test POS prints nothing anyway).
        # Accounts_place reads `original_current_price` (its own `current_price` is DEPRECATED/dropped).
        sale_rows = [{"article_id": r["article_id"], "amount": r["amount"],
                      "original_current_price": r["_price"]} for r in rows]
        place = {"account": account, "orders": sale_rows, "payments": payments,
                 "flag_close_account": _SALE_FLAG_CLOSE, "print_receipt": _SALE_PRINT_RECEIPT,
                 "print_shipment": _SALE_PRINT_SHIPMENT}
        inv = _invoice_data(order, account)
        if inv:                                            # buyer asked for a company VAT invoice (фактура)
            place["invoice_data"] = inv
            email = (order.get("customer") or {}).get("email") or order.get("email") or ""
            if email:                                      # so Barsy auto-emails the issued фактура
                account["email"] = email
                account["email_invoices"] = email
        aid = b.call("Accounts_place", place)
        _record_order(oid, aid, len(rows), "sale")
        return {**plan, "status": "ok", "account_id": aid, "paid_online": paid_online, "invoice": bool(inv),
                "closed": bool(_SALE_FLAG_CLOSE), "decremented": bool(_SALE_FLAG_CLOSE), "written": True}


# ⚠ STORNO = the dedicated `Accounts_storno` method (NOT Accounts_place). Confirmed from docs 2026-07-27:
# AccountInputData has NO `storno_of_account_id` field (only `storno_data`), so the old
# Accounts_place{account:{storno_of_account_id}} was passing an ignored field — it did NOT storno.
# Accounts_storno signature: {account_id:int, article_ids:[] (empty=whole account), payments:[]
# (empty=full refund), storno_data:{storno_reason}} → returns the storno account id. Valid
# storno_reason (Accounts_getstornoreasons): return | mistake | reduction_tax_base.
# ⚠⚠ CONSTRAINTS (docs): storno works ONLY on a CLOSED (приключена) account; the reason is sent to the
# FISCAL DEVICE (the storno account gets its own УНП + fiscal storno receipt); the op is IRREVERSIBLE.
# So storno PRESUPPOSES the sale was fiscalised/closed (the pending fiscal-timing step). While mode=sale
# only PLACES (open account), there is nothing to storno yet — the account_id must be closed first.
# NOT yet live-verified (fiscal + irreversible → needs a fiscal POS + explicit owner go).
def _account_storno_data(account_id, reason):
    """Build InputStornoData for Accounts_storno from the sale account's FISCAL-receipt fields. Storno
    REQUIRES all of: `unp` (format DDDDDDDD-UUUU-NNNNNNN), `receipt_num`, `receipt_date`, `fm_num`
    (8 digits) — all issued by the FISCAL DEVICE at close. On a device-less POS they're null → returns
    None (the connector then surfaces needs_review:no_unp — a non-fiscally-closed sale can't be
    stornoed). On a real fiscal POS they're populated → the storno reverses the sale + restores stock.
    (Proven live 2026-07-28: a correctly-FORMATTED placeholder set reversed stock 8→9, so Barsy
    validates field format+presence, not a receipt-registry lookup.)"""
    try:
        a = b.call("Accounts_get", {"account_id": int(account_id)}).get("account") or {}
    except Exception:
        return None
    if not a.get("unp"):
        return None
    return {"storno_reason": reason, "unp": a.get("unp"), "receipt_num": a.get("receipt_num"),
            "receipt_date": a.get("receipt_date"), "fm_num": a.get("fm_num")}


def shopify_order_cancellation_to_barsy(order, do_write=False, storno_reason="mistake"):
    """Order cancelled on Shopify → reverse it in Barsy per the owner's clientorder model:
    a still-CLIENTORDER order is VOIDED (Clientorders_setstatus → 6 Отказана; nothing was decremented,
    the refuse-before-finalise flow); a FINALISED sale is STORNO'd (Accounts_storno with the sale's unp).
    `storno_reason` ∈ {return, mistake, reduction_tax_base} — 'mistake' (операторска грешка) for a plain
    cancellation, 'return' (Връщане/рекламация) for an uncollected/returned COD (see cod_refused_to_barsy).
    ⚠ storno is fiscal+irreversible + needs the account's УНП (fiscal POS). Branch keys on the recorded
    `kind` — the finalise step MUST record kind='sale' + the new account_id for the storno branch to fire."""
    oid = str(order.get("id") or "")
    name = order.get("name") or f"#{oid}"
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"status": "refused", "reason": "no_logged_order", "order_ref": name,
                "skipped": "no logged Barsy order for this Shopify order", "order_id": oid}
    key = f"cancel-{oid}"
    if _seen_key(key):
        return {"status": "duplicate", "order_ref": name, "skipped": "already cancelled", "order_id": oid}
    # DURABLE re-route: after a recreate the log can rebuild from the order webhook as kind='clientorder'
    # while Barsy already holds a finalised SALE account (the panel finalise action isn't replayed). Voiding
    # a clientorder that's really a sale is wrong — ask Barsy and flip to the storno branch if so.
    if rec.get("kind") == "clientorder":
        healed = _barsy_account_for_clientorder(rec.get("account_id"))
        if healed:
            if do_write:
                record_finalised(oid, healed, rec.get("rows", 0))   # persist the repair only on a real run
                rec = _seen_order(oid)                              # now kind='sale', account_id=healed
            else:
                # DRY-RUN MUST NOT MUTATE: shadow the healed state in memory so the plan reflects reality
                # without persisting it (a dry-run previously changed what a later live cancel would do).
                rec = {**rec, "kind": "sale", "account_id": healed}  # (audit fix 2026-07-28)
    # CLIENTORDER stage → VOID (nothing was decremented) — the owner's refuse-before-finalise flow.
    if rec.get("kind") == "clientorder":
        cid = rec.get("account_id")                          # = client_order_id
        plan = {"order_id": oid, "order_ref": name, "client_order_id": cid,
                "method": "Clientorders_setstatus", "action": "void"}
        if not do_write:
            return {**plan, "dry_run": True}
        with _op_lock(f"cancel-{oid}"):
            if _seen_key(key):
                return {**plan, "status": "duplicate", "skipped": "already cancelled"}
            b.call("Clientorders_setstatus", {"client_order_id": int(cid), "status_id": 6})   # 6 = Отказана
            _record_order(oid, cid, 0, "cancelled", key=key)
            return {**plan, "status": "ok", "voided_clientorder": cid, "written": True}
    # FINALISED sale → storno the WHOLE account (article_ids=[]); needs the sale's unp.
    aid = rec["account_id"]
    plan = {"order_id": oid, "order_ref": name, "account_id": aid, "method": "Accounts_storno",
            "article_ids": [], "storno_reason": storno_reason}
    if not do_write:
        return {**plan, "dry_run": True}
    with _op_lock(f"cancel-{oid}"):
        if _seen_key(key):
            return {**plan, "status": "duplicate", "skipped": "already stornoed"}
        sd = _account_storno_data(aid, storno_reason)
        if not sd:
            return {**plan, "status": "needs_review", "reason": "no_unp",
                    "error": "account has no fiscal receipt/УНП (non-fiscal POS?) — storno needs the fiscal fields",
                    "needs_review": True}
        res = b.call("Accounts_storno", {"account_id": int(aid), "article_ids": [],
                     "payments": [], "storno_data": sd})
        _record_order(oid, aid, 0, "storno", key=key, storno_account_id=res)
        return {**plan, "status": "ok", "storno_account_id": res, "written": True}


def shopify_refund_to_barsy(refund, do_write=False):
    """Refund on Shopify → storno the refunded ARTICLES + restock. VERIFY-LIVE (fiscal).
    ⚠ Accounts_storno storns by ARTICLE (article_ids), NOT by quantity — a partial-QUANTITY refund
    (e.g. 1 of 3 units) can only be modelled as a whole-article storno here. Flag for fiscal-timing design."""
    oid = str(refund.get("order_id") or "")
    rid = refund.get("id")
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"status": "refused", "reason": "no_logged_sale", "order_ref": f"#{oid}",
                "skipped": "no logged sale", "order_id": oid}
    # A storno needs a SALE account. For a record still at kind='clientorder', `account_id` is the
    # CLIENT-ORDER id — passing it to Accounts_storno would target the wrong object entirely (or 501 on a
    # non-existent account). Nothing was decremented at clientorder stage anyway, so the correct reversal
    # is a VOID via the cancel-branch, not a storno. (found while testing the reversal invariant)
    if rec.get("kind") not in ("sale", "refund", "storno"):
        return {"status": "needs_review", "reason": "not_finalised", "order_ref": f"#{oid}", "order_id": oid,
                "detail": {"kind": rec.get("kind")},
                "error": f"order is at '{rec.get('kind')}' stage, not a finalised sale — a refund cannot be "
                         f"stornoed; void the clientorder instead", "needs_review": True}
    key = f"refund-{rid}"                                   # dedupe per refund (an order can have several)
    if _seen_key(key):
        return {"status": "duplicate", "order_ref": f"#{oid}", "skipped": "already refunded",
                "order_id": oid, "refund_id": rid}
    cat = _catalog()
    article_ids, partial_qty = [], False
    for ri in (refund.get("refund_line_items") or []):
        li = ri.get("line_item") or {}
        art = cat.get(str(li.get("sku") or ""))
        if not art:
            continue
        article_ids.append(int(art["article_id"]))
        # detect a partial-quantity refund (storno can't express it — whole article is reversed)
        if int(ri.get("quantity") or 0) < int(li.get("quantity") or ri.get("quantity") or 0):
            partial_qty = True
    plan = {"order_id": oid, "order_ref": f"#{oid}", "account_id": rec["account_id"], "method": "Accounts_storno",
            "article_ids": article_ids, "storno_reason": "return",
            **({"warnings": [{"kind": "partial_quantity_refund",
                              "reason": "storno reverses whole article; per-unit qty not expressible"}]}
               if partial_qty else {})}
    # NEVER fall through with an empty article list: `article_ids=[]` means "storno the WHOLE account" in
    # this API, so a shipping-only/goodwill refund or an unmapped SKU would irreversibly reverse the entire
    # (fiscal) sale and restock goods the customer kept. Refuse and let a human decide. (audit fix 2026-07-28)
    # Empty-list check FIRST — it is the more specific diagnosis. (`article_ids=[]` means "storno the WHOLE
    # account" in this API, so a shipping-only/goodwill refund or an unmapped SKU would irreversibly reverse
    # the entire fiscal sale and restock goods the customer kept.) Running the totals invariant first would
    # report a confusing "reversal_mismatch" for what is really an unmatched-SKU problem.
    if not article_ids:
        return {**plan, "status": "needs_review", "reason": "no_matched_articles",
                "error": "refund matched no Barsy article (shipping-only/goodwill refund or unmapped SKU) — "
                         "an empty article list would storno the WHOLE sale; not reversing",
                "needs_review": True}
    # REVERSAL INVARIANT (mirror of the intake totals backstop): what we REVERSE in Barsy must equal what
    # Shopify actually refunded. This is the more dangerous direction — an intake mismatch eventually
    # surfaces as a customer complaint or a courier remittance that won't reconcile, but an under/over
    # reversal is silent: nobody outside ever sees it, and it just leaves phantom stock (or phantom
    # returns) sitting in Barsy. Also HARD-STOPS the partial-quantity case that was previously only a
    # warning: Accounts_storno reverses WHOLE articles, so refunding 1 of 3 units would reverse all 3.
    _refund_total = sum(float(t.get("amount") or 0) for t in (refund.get("transactions") or [])) or \
        sum(float(ri.get("subtotal") or 0) + float(ri.get("total_tax") or 0)
            for ri in (refund.get("refund_line_items") or []))
    if _refund_total:
        try:
            _acc = b.call("Accounts_get", {"account_id": int(rec["account_id"]),
                                           "extra_properties": {"orders": "1"}}) or {}
            _rev_total = sum(float(o.get("current_price") or 0) * float(o.get("amount") or 0)
                             for o in (_acc.get("orders") or [])
                             if int(o.get("article_id") or 0) in article_ids)
        except Exception as _e:
            # FAIL CLOSED. A money guard that silently no-ops when it can't verify is worse than no guard —
            # it looks like it passed. If we cannot read what we'd reverse, we do not reverse.
            return {**plan, "status": "needs_review", "reason": "reversal_unverifiable",
                    "detail": {"shopify_refund": round(_refund_total, 2), "error": str(_e)[:150]},
                    "error": "could not read the Barsy account to verify the reversal total — NOT stornoed",
                    "needs_review": True}
        if abs(_rev_total - _refund_total) > 0.01:
            return {**plan, "status": "needs_review", "reason": "reversal_mismatch",
                    "detail": {"would_reverse": round(_rev_total, 2),
                               "shopify_refund": round(_refund_total, 2),
                               "difference": round(_rev_total - _refund_total, 2),
                               "suspect": "partial_quantity" if partial_qty else "unknown"},
                    "error": f"Barsy would reverse {_rev_total:.2f} but Shopify refunded "
                             f"{_refund_total:.2f} — NOT stornoed (would leave phantom stock)",
                    "needs_review": True}
    if not do_write:
        return {**plan, "dry_run": True}
    with _op_lock(f"refund-{rid}"):
        if _seen_key(key):
            return {**plan, "status": "duplicate", "skipped": "already refunded"}
        sd = _account_storno_data(rec["account_id"], "return")
        if not sd:
            return {**plan, "status": "needs_review", "reason": "no_unp",
                    "error": "account has no fiscal receipt/УНП (non-fiscal POS?) — storno needs the fiscal fields",
                    "needs_review": True}
        res = b.call("Accounts_storno", {"account_id": int(rec["account_id"]), "article_ids": article_ids,
                     "payments": [], "storno_data": sd})
        _record_order(oid, rec["account_id"], len(article_ids), "refund", key=key, storno_account_id=res)
        return {**plan, "status": "ok", "storno_account_id": res, "written": True}


# ---- panel review→finalise / refuse buttons (staff actions on the clientorder queue) --------------
def finalise_order(order_id, do_write=False, tracking=None, speditor=None):
    """Panel FINALISE action (fires on the Етикет/label step): convert a logged CLIENTORDER into a CLOSED,
    PAID sale. Closes the anonymous account with a payment under the "Наложен платеж" method (id 6) so it
    (a) decrements stock and (b) prints the fiscal бон tagged наложен платеж (print_receipt:1). Anonymous
    accounts can't OWE, so COD is booked as paid-at-packing; the panel's courier-remittance reconciliation
    catches any uncollected order and stornos it. Idempotent: reconciles against Barsy so a repeat click /
    post-restart returns already_finalised instead of double-decrementing. Gated: do_write=False → dry-run.
    Optional `tracking` (courier waybill) + `speditor` (courier name e.g. "Спиди"/"Еконт") are written onto
    the Barsy sale account IF supplied — but a CLOSED account can't be backfilled, so they only land when
    passed HERE (i.e. the panel must have the waybill at finalise time = label-first ordering). Omit them and
    the current finalise-first flow is unchanged; the panel then matches COD by its own state_map."""
    oid = str(order_id)
    rec = _seen_order(oid)
    name = (rec or {}).get("order_ref") or f"#{oid}"
    if not rec:
        return {"status": "refused", "reason": "no_logged_order", "order_ref": name, "order_id": oid}
    if rec.get("kind") == "sale":
        return {"status": "duplicate", "reason": "already_finalised", "order_ref": name,
                "order_id": oid, "account_id": rec.get("account_id")}
    if rec.get("kind") != "clientorder":
        return {"status": "needs_review", "reason": "not_a_clientorder",
                "detail": {"kind": rec.get("kind")}, "order_ref": name, "order_id": oid}
    cid = rec.get("account_id")                            # = client_order_id
    plan = {"order_id": oid, "order_ref": name, "client_order_id": cid,
            "method": "Accounts_createfromclientorder", "paymethod": "Наложен платеж"}
    if not do_write:
        # Reconcile the DRY-RUN against Barsy too (not just the local log): after a restart the log rebuilds
        # co as kind='clientorder' while Barsy may already hold the sale account — report already_finalised so
        # the panel's finalise-before-label guard is TIGHT (doesn't falsely force a harmless re-finalise).
        existing = _barsy_account_for_clientorder(cid)
        if existing:
            return {**plan, "status": "already_finalised", "account_id": existing, "dry_run": True}
        return {**plan, "status": "ok", "dry_run": True}
    with _op_lock(f"finalise-{oid}"):
        rec2 = _seen_order(oid)                            # re-check under lock (not finalised concurrently)
        if rec2 and rec2.get("kind") == "sale":
            return {**plan, "status": "duplicate", "account_id": rec2.get("account_id"),
                    "skipped": "already finalised"}
        # DURABLE guard: ORDERS_STATE.jsonl lives in the container FS and is wiped by a recreate, so ask
        # Barsy directly — if the clientorder already has a sale account, it was finalised in a prior life;
        # self-heal the local log and return calmly instead of re-closing (which Barsy would 501 anyway).
        existing = _barsy_account_for_clientorder(cid)
        if existing:
            record_finalised(oid, existing, rec.get("rows", 0))   # repair wiped state
            return {**plan, "status": "already_finalised", "account_id": existing,
                    "skipped": "barsy already has a sale account for this clientorder"}
        # Pay the EXACT account total under the COD method → closes PAID (an anonymous account can't owe) and
        # prints the fiscal бон. Read the total from the clientorder; without it we can't build the payment,
        # and an unpaid anonymous close would 501 — so surface needs_review rather than fail obscurely.
        total = (b.call("Clientorders_get", {"client_order_id": int(cid)}) or {}).get("total_sum")
        if total in (None, ""):
            return {**plan, "status": "needs_review", "reason": "no_total",
                    "error": "could not read clientorder total to book the COD payment", "needs_review": True}
        pay = [{"paymethod_id": COD_PAYMETHOD_ID, "actual_paid_sum": str(total)}]
        props = {"uuid": _acc_uuid(f"shopify-finalise-{oid}")}
        # tracking_number/speditor_id are AccountInputData fields → they belong INSIDE account_props. At the
        # top level of the createfromclientorder body they are silently ignored. (audit fix 2026-07-28)
        if tracking:                                       # waybill onto the Barsy account (label-first flow)
            props["tracking_number"] = str(tracking)
        if speditor:
            sid = _speditor_id(speditor)                   # AccountInputData takes the ID, not the name
            if sid:
                props["speditor_id"] = sid
        # B2B VAT invoice: issue the фактура with the sale. `invoice_data` is a PaymentSaveData field —
        # it rides ON THE PAYMENT, not at the top level of the request (verified in the docs; passing it
        # top-level is silently ignored and no фактура is created). Prefer the data captured at intake;
        # fall back to re-reading Shopify (ORDERS_STATE is rebuilt without it after a container recreate).
        inv = rec.get("invoice") or _invoice_for_order(oid)
        if inv:
            pay[0]["invoice_data"] = inv
        body = {"client_order_id": int(cid), "account_props": props,
                "flag_close_account": 1, "print_shipment": 0, "print_receipt": 1, "payments": pay}
        try:
            aid = b.call("Accounts_createfromclientorder", body)
        except Exception as e:
            # Belt-and-suspenders: even if the pre-check missed it (race / partial read), Barsy is the true
            # backstop — its "вече има създадена ... сметка" 501 means no second decrement happened.
            msg = str(e)
            if "вече има създадена" in msg or "покриваща" in msg or "покриващи" in msg:
                existing = _barsy_account_for_clientorder(cid)
                if existing:
                    record_finalised(oid, existing, rec.get("rows", 0))
                return {**plan, "status": "already_finalised", "account_id": existing,
                        "skipped": "barsy rejected re-finalise (clientorder already has an account)",
                        "barsy_message": msg[:200]}
            raise
        account_id = aid.get("account_id") if isinstance(aid, dict) else aid
        record_finalised(oid, account_id, rec.get("rows", 0))
        out = {**plan, "status": "ok", "account_id": account_id, "decremented": True,
               "paid": total, "written": True}
        # B2B фактура — BEST EFFORT. The sale is already closed and fiscalised; an invoice problem must
        # never fail the finalise (the request stays in list_invoice_requests for manual issuing).
        if inv:
            try:
                out["invoice"] = issue_invoice(oid, do_write=True)
            except Exception as e:
                out["invoice"] = {"status": "needs_review", "reason": "invoice_error",
                                  "error": str(e)[:200], "needs_review": True}
        return out


def refuse_order(order_id, do_write=False):
    """Panel REFUSE action: cancel the Shopify order (releases the reservation + drops it from the queue)
    AND void/storno the Barsy side via the cancel-branch (clientorder→void status 6; finalised→storno).
    Dedup-safe with the orders/cancelled webhook the Shopify-cancel also fires (both key on cancel-<oid>).
    restock=False on the Shopify cancel — Barsy is authoritative for stock. Gated: do_write=False → dry-run."""
    oid = str(order_id)
    rec = _seen_order(oid)
    name = (rec or {}).get("order_ref") or f"#{oid}"
    plan = {"order_id": oid, "order_ref": name}
    if not do_write:
        return {**plan, "status": "ok", "dry_run": True,
                "barsy_plan": shopify_order_cancellation_to_barsy({"id": oid, "name": name}, do_write=False)}
    shop = {}
    try:
        d = s.gql("""mutation($id:ID!,$reason:OrderCancelReason!,$restock:Boolean!,$notify:Boolean){
                     orderCancel(orderId:$id, reason:$reason, restock:$restock, notifyCustomer:$notify){
                       job{ id } orderCancelUserErrors{ field message } } }""",
                  {"id": f"gid://shopify/Order/{oid}", "reason": "STAFF", "restock": False, "notify": False})
        errs = (d.get("orderCancel") or {}).get("orderCancelUserErrors") or []
        shop = {"cancelled": not errs, **({"errors": errs} if errs else {})}
    except Exception as e:
        shop = {"cancelled": False, "error": str(e)[:150]}
    # void/storno the Barsy side (reuses the cancel-branch; deduped vs the orders/cancelled webhook)
    barsy = shopify_order_cancellation_to_barsy({"id": oid, "name": name}, do_write=True)
    # A FAILED Shopify cancel must NOT report ok: Barsy would be voided while the Shopify order stays live,
    # holding its reservation and reappearing in the queue. Surface it for review. (audit fix 2026-07-28)
    if not shop.get("cancelled"):
        return {**plan, "status": "needs_review", "reason": "shopify_cancel_failed",
                "error": "Barsy side reversed but the Shopify order was NOT cancelled — it still holds stock",
                "needs_review": True, "shopify_cancel": shop, "barsy": barsy}
    return {**plan, "status": barsy.get("status", "ok"), "shopify_cancel": shop, "barsy": barsy}


# ---- COD reconciliation hooks for the panel (courier remittances ↔ Barsy method-6 payments) ----------
def _all_latest_orders():
    """{order_id: latest ORDERS_STATE record} — the newest logged state per order (clientorder/sale/storno)."""
    latest = {}
    for d in _state_records():
        latest[str(d.get("order_id"))] = d
    return latest


_SHOPIFY_DESC = re.compile(r"Shopify order\s+(\S+)\s+\((\d+)\)")


def list_cod_accounts(since=None):
    """READ hook for the panel's COD reconciliation: every FINALISED sale (under the anonymous-paid COD
    model each finalised order is a Наложен-платеж/method-6 sale) with the fields needed to match against a
    courier payout report. Panel joins its OWN courier waybill by order_id (Barsy's tracking_number isn't
    populated — finalise-first flow, see finalise_order). `since` = ISO date/ts filter on close_date. No writes.

    Driven off BARSY (`Accounts_getlist` + the "Shopify order <name> (<id>)" description finalise stamps on
    each account), NOT the local ORDERS_STATE log: that log is rebuilt from webhooks after a container
    recreate WITHOUT the panel-action 'sale' records, so a state-driven version silently returned [] after
    every redeploy — i.e. the panel's reconciliation would report zero storno candidates. (audit fix
    2026-07-28) ORDERS_STATE is still consulted, but only to enrich rows Barsy can't name."""
    latest = _all_latest_orders()
    by_acct = {str(r.get("account_id")): (oid, r) for oid, r in latest.items() if r.get("kind") == "sale"}
    rows = []
    for a in (b.call("Accounts_getlist", {"filters": {}}) or []):
        if not a.get("close_date"):                        # open/unclosed account = not a finalised sale
            continue
        aid = str(a.get("account_id"))
        m = _SHOPIFY_DESC.search(str(a.get("description") or ""))
        state_oid, rec = by_acct.get(aid, (None, {}))
        oid = (m.group(2) if m else None) or state_oid
        if not oid:                                        # not one of ours (walk-in POS sale etc.)
            continue
        if since and str(a.get("close_date") or "") < str(since):
            continue
        total = a.get("total_sum")
        # Skip STORNO COUNTER-DOCUMENTS: a storno creates its own account with a NEGATIVE total that
        # inherits the original's "Shopify order …" description. It is not collectable COD — listing it
        # would double-count the order (once positive, once negative) in the panel's expected-COD totals.
        # The reversal is already reported on the ORIGINAL row via `stornoed`.
        if total is not None and float(total) < 0:
            continue
        storno = a.get("total_sum_storno") or 0
        # a fully-reversed account (|storno| >= total) is already cancelled — surface it so the panel skips it
        reversed_ = bool(total) and abs(float(storno)) >= abs(float(total)) and float(total) != 0
        rows.append({
            "order_id": str(oid),
            "order_ref": (m.group(1) if m else None) or rec.get("order_ref") or f"#{oid}",
            "account_id": a.get("account_id"), "amount": total,
            "paid": a.get("total_paid"),                   # paid-at-finalise ⇒ == amount; 0 = legacy/owed
            "close_date": a.get("close_date"), "finalised_ts": rec.get("ts") or a.get("close_date"),
            "tracking_number": a.get("tracking_number"),   # None today — panel matches via its own state
            "stornoed": reversed_,
        })
    return rows


_SELLER = {"data": None}


def _seller_company():
    """Our own company header for a фактура (cached). Barsy REQUIRES the issuer's ЕИК/МОЛ — if these are
    blank in Настройки → Основни → Фирми the invoice cannot be issued."""
    if _SELLER["data"] is None:
        try:
            _SELLER["data"] = (b.call("Companies_getlist", {"filters": {}}) or [{}])[0]
        except Exception:
            _SELLER["data"] = {}
    return _SELLER["data"]


def _invoice_rows(account_id):
    """Build Invoices_save `rows` from the CLOSED account's order lines. Shape reverse-engineered by
    capturing the admin UI's own request (the API is undocumented) — see the memory note. Prices on the
    account are VAT-INCLUSIVE (`current_price`), so the ex-VAT unit price is derived."""
    acc = b.call("Accounts_get", {"account_id": int(account_id),
                                  "extra_properties": {"orders": "1"}}) or {}
    code_by_id = {str(a.get("article_id")): str(a.get("code") or "")
                  for a in (b.call("Articles_getlist", {"filters": {}}) or [])}
    rows = []
    for i, o in enumerate(acc.get("orders") or [], 1):
        qty = float(o.get("amount") or o.get("row_amount") or 0)
        gross_unit = float(o.get("current_price") or o.get("original_current_price") or 0)
        tax = float(o.get("tax") or 0)
        tax_id = str(o.get("tax_id") or 2)
        net_unit = gross_unit / (1 + tax / 100) if tax else gross_unit
        total = round(gross_unit * qty, 2)
        rows.append({
            "item_id": str(i), "article_id": str(o.get("article_id") or ""),
            "inv_ref_num": code_by_id.get(str(o.get("article_id")), ""),
            "item_name": o.get("article_name") or "",
            "quantity": str(int(qty) if qty == int(qty) else qty),
            "measure": o.get("amount_type_name_short") or "бр",
            "original_single_price": f"{net_unit:.6f}".rstrip("0").rstrip("."),
            "original_single_price_with_tax": f"{gross_unit:.2f}",
            "discount": "0", "tax": str(int(tax)), "tax_id": tax_id, "tax_reason": "",
            f"total_for_tax_{tax_id}": f"{total:.2f}",
            "total_price": f"{total:.2f}", "chooser": 0,
        })
    return rows, acc


def issue_invoice(order_id, do_write=False):
    """Issue the B2B VAT invoice (фактура) for a FINALISED order via the UNDOCUMENTED `Invoices_save`
    (contract captured from the admin UI — there is no documented create method). Barsy validates hard:
    the receiver's company name, ЕИК and ADDRESS must all be non-empty, and our own company must have its
    ЕИК/МОЛ set. Returns needs_review (never raises) when anything is missing, so the request stays visible
    in list_invoice_requests() for manual issuing instead of vanishing. Gated: do_write=False → dry-run."""
    oid = str(order_id)
    rec = _seen_order(oid)
    name = (rec or {}).get("order_ref") or f"#{oid}"
    if not rec or rec.get("kind") != "sale" or not rec.get("account_id"):
        return {"status": "refused", "reason": "not_a_finalised_sale", "order_ref": name, "order_id": oid}
    inv = next((d["invoice"] for d in _state_records()
                if str(d.get("order_id")) == oid and d.get("invoice")), None) or _invoice_for_order(oid)
    if not inv:
        return {"status": "skipped", "reason": "no_invoice_requested", "order_ref": name, "order_id": oid}
    key = f"invoice-{oid}"
    if _seen_key(key):
        return {"status": "duplicate", "skipped": "invoice already issued", "order_id": oid}
    missing = [k for k, src in (("фирма", "receiver_company_name"), ("ЕИК", "receiver_identity_num"),
                                ("адрес", "receiver_address")) if not str(inv.get(src) or "").strip()]
    seller = _seller_company()
    if not str(seller.get("identity_num") or "").strip():
        missing.append("ЕИК на издателя (Настройки→Фирми)")
    plan = {"order_id": oid, "order_ref": name, "account_id": rec["account_id"], "method": "Invoices_save"}
    if missing:
        return {**plan, "status": "needs_review", "reason": "incomplete_invoice_data",
                "error": f"липсват задължителни данни за фактура: {', '.join(missing)}",
                "detail": {"missing": missing}, "needs_review": True}
    if not do_write:
        return {**plan, "status": "ok", "dry_run": True}
    with _op_lock(f"invoice-{oid}"):
        if _seen_key(key):
            return {**plan, "status": "duplicate", "skipped": "invoice already issued"}
        rows, acc = _invoice_rows(rec["account_id"])
        if not rows:
            return {**plan, "status": "needs_review", "reason": "no_rows",
                    "error": "сметката няма редове за фактуриране", "needs_review": True}
        today = time.strftime("%Y-%m-%d")
        values = {
            "type_id": "1",                                # 1 = Фактура
            "client_id": acc.get("client_id") or _ANON_CLIENT_ID,
            "client_name": acc.get("client_name") or "",
            "company_id": seller.get("company_id") or 1, "name": seller.get("name") or "",
            "seller_company_id": seller.get("company_id") or 1,
            "seller_company_name": seller.get("company_name") or "",
            "seller_address": seller.get("address") or "", "seller_town": seller.get("town") or "",
            "seller_identity_num": seller.get("identity_num") or "",
            "seller_vat_num": seller.get("vat_num") or "", "seller_mol": seller.get("mol") or "",
            "seller_country_id": "BG",
            "receiver_company_name": inv.get("receiver_company_name") or "",
            "receiver_address": inv.get("receiver_address") or "",
            "receiver_town": inv.get("receiver_town") or "",
            "receiver_identity_num": inv.get("receiver_identity_num") or "",
            "receiver_vat_num": inv.get("receiver_vat_num") or "",
            "receiver_mol": inv.get("receiver_mol") or "", "receiver_country_id": "BG",
            "inv_id": None, "inv_num": "", "parent_inv_id": None,
            "create_date": today, "term_date": today, "payment_date": today,
            "discount": "0", "with_tax": 0, "currency_id": "1", "currency_rate": "1",
            "is_anulate": "0", "deal_id": None, "deal_title": "", "person_id": None, "person_name": "",
        }
        res = b.call("Invoices_save", {"id": None, "action_type": "save",
                                       "values": values, "rows": rows})
        _record_order(oid, rec["account_id"], len(rows), "invoice", key=key, invoice_result=res)
        return {**plan, "status": "ok", "invoice": res, "rows": len(rows), "written": True}


_INV_KEYS = (("name", "receiver_company_name"), ("eik", "receiver_identity_num"),
             ("vat", "receiver_vat_num"), ("mol", "receiver_mol"),
             ("city", "receiver_town"), ("address", "receiver_address"))


def list_invoice_requests(since=None):
    """READ hook for the panel's "Фактури за издаване" screen. Barsy's API CANNOT issue a фактура (there is
    no create method; `invoice_data` is accepted but only auto-issues for VPOS payments), so staff issue it
    manually via Документи → Фактури → "Нова фактура по сметка(и)". This returns every finalised order that
    REQUESTED one, with the copy-ready company block and — most importantly — the **account_id** staff pick
    in that dialog. No writes. `issued` is always False: the connector owns WHICH orders asked, the panel
    owns which are done.

    Recreate-proof: driven off the Barsy accounts (like list_cod_accounts). The company block comes from our
    intake capture when present, else Barsy's `with_invoice` flag on the clientorder tells us to re-read the
    request from Shopify. Missing fields are returned as "" (never omitted) so the UI can render a stable
    block and flag what's incomplete — a фактура with a blank ЕИК is useless."""
    accs = {str(a.get("account_id")): a for a in (b.call("Accounts_getlist", {"filters": {}}) or [])}
    # The invoice block is captured on the CLIENTORDER record at intake; finalise then appends a fresh
    # 'sale' record without it. So scan ALL records per order — reading only the latest one misses it.
    captured = {}
    for d in _state_records():
        if d.get("invoice"):
            captured[str(d.get("order_id"))] = d["invoice"]
    out = []
    for row in list_cod_accounts(since=since):
        if row.get("stornoed"):                            # reversed sale — nothing to invoice
            continue
        aid = str(row.get("account_id"))
        inv = captured.get(str(row["order_id"]))
        if not inv:
            # No local capture (e.g. state rebuilt after a recreate) — ask Barsy whether this sale's
            # clientorder carried the with_invoice flag before spending a Shopify read.
            wants = False
            for co_id in str((accs.get(aid) or {}).get("client_order_ids") or "").split(","):
                co_id = co_id.strip()
                if not co_id:
                    continue
                try:
                    if str((b.call("Clientorders_get", {"client_order_id": int(co_id)}) or {})
                           .get("with_invoice") or "") == "1":
                        wants = True
                except Exception:
                    pass
            if wants:
                inv = _invoice_for_order(row["order_id"])
        if not inv:
            continue
        out.append({
            "order_id": row["order_id"], "order_ref": row["order_ref"],
            "account_id": row["account_id"],               # ← what staff type into "Нова фактура по сметка(и)"
            "amount": row["amount"],
            "company": {k: str(inv.get(src) or "") for k, src in _INV_KEYS},
            "requested_ts": row.get("finalised_ts") or row.get("close_date"),
            # True once WE issued it via Invoices_save; the panel still owns "marked done by staff".
            "issued": _seen_key(f"invoice-{row['order_id']}"),
        })
    return out


def list_sellable_variants():
    """Read-only publish-readiness audit feed (#81): paginate ALL Shopify variants → per variant
    {variant_id, sku, product_title, product_status, tracked, policy, on_hand}. The panel cross-checks
    each sku against the Barsy-code mirror and flags no_sku / sku_not_in_barsy / not_tracked / not_active."""
    out, cursor = [], None
    while True:
        d = s.gql("""query($cur:String){ productVariants(first:100, after:$cur){
                     pageInfo{ hasNextPage endCursor }
                     nodes{ id sku inventoryPolicy product{ title status }
                       inventoryItem{ tracked inventoryLevels(first:1){ nodes{
                         quantities(names:["on_hand"]){ quantity } } } } } } }""", {"cur": cursor})
        pv = d.get("productVariants") or {}
        for n in pv.get("nodes") or []:
            ii = n.get("inventoryItem") or {}
            lvls = (ii.get("inventoryLevels") or {}).get("nodes") or []
            qs = (lvls[0].get("quantities") if lvls else None) or []
            prod = n.get("product") or {}
            out.append({"variant_id": n.get("id"), "sku": n.get("sku"),
                        "product_title": prod.get("title"), "product_status": prod.get("status"),
                        "tracked": ii.get("tracked"), "policy": n.get("inventoryPolicy"),
                        "on_hand": (qs[0].get("quantity") if qs else None)})
        pi = pv.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            return out
        cursor = pi.get("endCursor")


def ship_order(order_id, tracking=None, do_write=False, speditor=None):
    """Ship/dispatch (#83, RECONCILIATION_SPEC rule B): create a Shopify `fulfillment` for the order's
    open fulfillment-orders (with the label's tracking #) — this CLEARS `committed` and drops on_hand ONCE,
    so a sold unit is counted exactly once vs the Barsy on_hand mirror. Called from the panel's label-success
    path. ALSO writes the waybill (+courier) onto the already-CLOSED Barsy sale account, so Barsy↔courier
    remittances can be matched directly by waybill. Dry-run returns the plan; {status, reason} → Проблеми."""
    oid = str(order_id)
    gid = f"gid://shopify/Order/{oid}"
    d = s.gql("""query($id:ID!){ order(id:$id){ name displayFulfillmentStatus
                 fulfillmentOrders(first:10){ nodes{ id status } } } }""", {"id": gid})
    order = d.get("order")
    if not order:
        return {"status": "refused", "reason": "order_not_found", "order_id": oid}
    name = order.get("name") or f"#{oid}"
    fos = [fo for fo in ((order.get("fulfillmentOrders") or {}).get("nodes") or [])
           if fo.get("status") in ("OPEN", "IN_PROGRESS")]
    plan = {"order_id": oid, "order_ref": name, "tracking": tracking,
            "fulfillment_orders": [fo["id"] for fo in fos]}
    if not fos:
        return {**plan, "status": "needs_review", "reason": "no_open_fulfillment_order",
                "detail": {"fulfillment_status": order.get("displayFulfillmentStatus")}}
    if not do_write:
        return {**plan, "status": "ok", "dry_run": True}
    results, ok = [], True
    for fo in fos:
        ffin = {"lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo["id"]}], "notifyCustomer": False}
        if tracking:
            ffin["trackingInfo"] = {"number": str(tracking)}
        try:
            r = s.gql("""mutation($f:FulfillmentInput!){ fulfillmentCreate(fulfillment:$f){
                         fulfillment{ id status } userErrors{ field message } } }""", {"f": ffin})
            fc = r.get("fulfillmentCreate") or {}
            errs = fc.get("userErrors") or []
            results.append({"fulfillment_order": fo["id"], "fulfillment": fc.get("fulfillment"),
                            **({"errors": errs} if errs else {})})
            ok = ok and not errs
        except Exception as e:
            results.append({"fulfillment_order": fo["id"], "error": str(e)[:150]})
            ok = False
    # Stamp the waybill onto the Barsy sale account. A CLOSED account DOES accept this via the NESTED
    # `account:{…}` shape (verified live 2026-07-28 — the top-level form is silently ignored, which is what
    # made an earlier probe look like "closed accounts can't be backfilled"). Best-effort: the Shopify
    # fulfilment is the authoritative part of this step, so a Barsy stamp failure must not fail the ship.
    barsy_stamp = None
    if tracking:
        rec = _seen_order(oid)
        if rec and rec.get("account_id") and rec.get("kind") in ("sale", "fulfillment", "storno", "refund"):
            acct = {"tracking_number": str(tracking)}
            sid = _speditor_id(speditor) if speditor else None
            if sid:
                acct["speditor_id"] = sid
            try:
                b.call("Accounts_place", {"account_id": int(rec["account_id"]), "account": acct,
                                          "orders": [], "payments": []})
                barsy_stamp = {"account_id": rec["account_id"], "tracking_number": tracking,
                               **({"speditor_id": sid} if sid else {}), "written": True}
            except Exception as e:
                barsy_stamp = {"account_id": rec["account_id"], "error": str(e)[:150], "written": False}
    return {**plan, "status": "ok" if ok else "needs_review",
            **({} if ok else {"reason": "fulfillment_failed"}), "fulfillments": results,
            **({"barsy_tracking": barsy_stamp} if barsy_stamp else {}), "written": True}


def shopify_fulfillment_to_barsy(order, do_write=False):
    """Order fulfilled/shipped on Shopify → write tracking number onto the Barsy account. VERIFY-LIVE."""
    # A fulfillments/create|update payload's top-level `id` is the FULFILLMENT id — the order is `order_id`.
    # (Using `id` here meant _seen_order never matched → tracking never written.) Tracking is top-level too.
    oid = str(order.get("order_id") or order.get("id") or "")
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"skipped": "no logged sale", "order_id": oid}
    ful = (order.get("fulfillments") or [{}])[0]
    track = order.get("tracking_number") or ful.get("tracking_number") or ""
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
    """A REFUSED/uncollected COD delivery (RTO) → storno the Barsy sale so the returned goods go back into
    stock. Mechanically identical to a Shopify cancellation (idempotent, keyed). The panel calls this from
    the COD reconciliation "uncollected → storno" action. `reason` is the human note; the Barsy fiscal
    storno_reason is fixed to 'return' (Връщане/рекламация) — the correct enum for a returned COD."""
    return shopify_order_cancellation_to_barsy({"id": str(oid), "cancel_reason": reason},
                                               do_write=do_write, storno_reason="return")


def cod_payment_to_barsy(oid, amount, paymethod_id=5, waybill=None, do_write=False):
    """⚠ OBSOLETE under the anonymous paid-at-packing COD model (2026-07-28). finalise_order already closes
    the sale PAID IN FULL (method 6), so posting a settlement payment here would OVERPAY the account
    (total_paid > total_sum) = double-count the COD cash. The panel's reconciliation now CONFIRMS a matched
    remittance without any Barsy write. Kept only for a hypothetical partially-owed account; it HARD-GUARDS
    on total_remain and no-ops if the account is already settled. paymethod_id 5 = По банка."""
    oid = str(oid)
    rec = _seen_order(oid)
    if not rec or not rec.get("account_id"):
        return {"skipped": "no logged Barsy sale for this order", "order_id": oid}
    key = f"codpay-{oid}"
    if _seen_key(key):
        return {"skipped": "COD payment already posted", "order_id": oid}
    # DOUBLE-PAY GUARD: refuse to post if the account isn't actually owed. Under paid-at-finalise every
    # account is already total_remain 0, so this makes the call a safe no-op even if wired in by mistake.
    try:
        acc = b.call("Accounts_get", {"account_id": int(rec["account_id"])}) or {}
        remain = float(acc.get("total_remain") or 0)
    except Exception:
        remain = None
    if remain is not None and remain <= 0:
        return {"skipped": "account already paid (paid-at-finalise model) — no COD payment to post",
                "order_id": oid, "account_id": rec["account_id"], "total_remain": remain, "guard": "double_pay"}
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
    status = sync.shopify_status(art)                     # ACTIVE/DRAFT from Barsy flags, or None
    if status is None:
        return {"skipped": "not sellable in Barsy (is_for_sale=0 / service / deleted)", "code": code}
    pin = {"title": _display_name(art) or code, "status": status}
    plan = {"code": code, "title": pin["title"], "price": _price(art), "status": status}
    if not do_write:
        return {**plan, "dry_run": True}
    d = s.gql("""mutation($p:ProductCreateInput!){ productCreate(product:$p){
                 product{ id variants(first:1){ nodes{ id } } } userErrors{ message } } }""", {"p": pin})
    pid = d["productCreate"]["product"]["id"]
    vid = d["productCreate"]["product"]["variants"]["nodes"][0]["id"]
    inv = {"sku": code, "tracked": False}
    wm = sync._weight_input((art.get("extra_data") or {}).get("weight_kg"))   # parcel weight (or omit)
    if wm:
        inv["measurement"] = wm
    s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid,
             variants:$v){ userErrors{ message } } }""",
          {"pid": pid, "v": [{"id": vid, "price": _price(art) or "0", "inventoryItem": inv}]})
    return {**plan, "product_id": pid, "written": True}


def barsy_article_to_shopify(code, do_write=False, force=False):
    """Barsy article change → Shopify UPSERT + status sync — the AUTO-SYNC entry point (fires on the Barsy
    `articles` webhook). CREATE if new, UPDATE name/price/weight if existing, set the product STATUS from the
    flags (is_for_sale/is_public → ACTIVE/DRAFT), and ARCHIVE when Barsy marks it not-for-sale / service /
    deleted (or the article is gone from Barsy). Echo-guarded. (owner auto-sync 2026-07-28)"""
    code = str(code)
    art = _catalog().get(code)
    # The shipping-fee article is BARSY-SIDE ONLY. It is is_service=1, so shopify_status() returns None and
    # the archive branch below would ARCHIVE any Shopify product carrying this SKU. If the storefront ever
    # sells delivery as a cart LINE ITEM (needed on Basic, where live courier rates can't feed the native
    # shipping row), that variant must carry SKU=DELIVERY to book against article 11365 — and this sync
    # would silently archive it mid-trading, breaking checkout. Never sync or archive it either way.
    if code == SHIPPING_CODE:
        return {"skipped": "shipping-fee article — Barsy-side only, never synced or archived", "code": code}
    pid, vid = _find_shopify(code)

    def _archive(reason):
        if not pid:
            return {"skipped": f"{reason} — not on Shopify", "code": code}
        if not do_write:
            return {"code": code, "product_id": pid, "action": "archive", "reason": reason, "dry_run": True}
        s.gql("mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ message } } }",
              {"p": {"id": pid, "status": "ARCHIVED"}})
        return {"code": code, "product_id": pid, "archived": True, "reason": reason, "written": True}

    if not art:                                            # gone from the live Barsy catalog (deleted)
        return _archive("gone_from_barsy") if pid else {"skipped": "not in Barsy", "code": code}
    status = sync.shopify_status(art)
    if status is None:                                     # not-for-sale / service / deleted
        return _archive("not_sellable")
    if not pid:                                            # not on Shopify yet → AUTO-CREATE
        return {"code": code, "action": "create", **barsy_new_product_to_shopify(code, do_write=do_write)}
    # existing → update name/price/weight + status. Key the echo on the DISPLAY name (public-preferred) so a
    # change to only article_name_public still triggers a push instead of being missed by the loop-guard.
    content = {"n": _display_name(art), "p": _price(art), "st": status}
    # `force` bypasses the loop-guard for the reconcile() sweep: there the drift is on the SHOPIFY side, so
    # Barsy's fingerprint is unchanged and the guard would skip the very fix we just decided to make — the
    # nightly drift repair silently did nothing. The guard still protects the webhook path. (audit fix)
    if not force and _echo(code, content):
        return {"skipped": "echo/no-op (loop-guard)", "code": code}
    title, price = _display_name(art), _price(art).strip()
    plan = {"code": code, "product_id": pid, "title": title, "price": price, "status": status}
    if not do_write:
        return {**plan, "dry_run": True}
    upd = {"id": pid, "status": status}                    # always sync status; title only if present
    if title:
        upd["title"] = title
    s.gql("mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ message } } }", {"p": upd})
    if price and vid:
        vin = {"id": vid, "price": price}
        wm = sync._weight_input((art.get("extra_data") or {}).get("weight_kg"))   # keep weight in sync too
        if wm:
            vin["inventoryItem"] = {"measurement": wm}
        s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(
                 productId:$pid, variants:$v){ userErrors{ message } } }""",
              {"pid": pid, "v": [vin]})
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
    # 1) inventoryPolicy on the variant; 2) enable tracking; 3) set on-hand at the primary location.
    # Read the current on_hand too — `InventoryQuantityInput` takes `changeFromQuantity` as its
    # optimistic-concurrency guard (VERIFIED by live introspection 2026-07-28: the input's only optional
    # field; there is NO `compareQuantity` here — that name belongs to `inventoryAdjustQuantities`. The old
    # `inventorySetOnHandQuantities` / an `ignoreCompareQuantity` flag both error in this API version).
    # We set on_hand ONLY → Shopify keeps available = on_hand − committed, so live order-reservations
    # survive every sync (reservation-safe). (fixed 2026-07-28)
    loc = _shopify_location()
    inv = s.gql("""query($id:ID!,$loc:ID!){ productVariant(id:$id){ inventoryItem{ id
            inventoryLevel(locationId:$loc){ quantities(names:["on_hand"]){ quantity } } } } }""",
          {"id": vid, "loc": loc})
    ii = inv["productVariant"]["inventoryItem"]
    inv_item = ii["id"]
    _lvl = ii.get("inventoryLevel") or {}
    cur_onhand = ((_lvl.get("quantities") or [{}])[0] or {}).get("quantity", 0) if _lvl else 0
    s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid,
             variants:$v){ userErrors{ message } } }""",
          {"pid": pid, "v": [{"id": vid, "inventoryPolicy": policy,
                              "inventoryItem": {"tracked": True}}]})
    s.gql("""mutation($id:ID!){ inventoryItemUpdate(id:$id, input:{tracked:true}){ userErrors{ message } } }""",
          {"id": inv_item})
    # API 2026-07 requires the @idempotent(key) directive on inventorySetQuantities; a fresh key per
    # call is fine (a stock SET is naturally idempotent + guarded by changeFromQuantity).
    s.gql("""mutation($in:InventorySetQuantitiesInput!,$key:String!){
             inventorySetQuantities(input:$in) @idempotent(key:$key){ userErrors{ field message } } }""",
          {"in": {"name": "on_hand", "reason": "correction",
                  "quantities": [{"inventoryItemId": inv_item, "locationId": loc,
                                  "quantity": int(float(qty or 0)), "changeFromQuantity": cur_onhand}]},
           "key": _uuid.uuid4().hex})
    # 4) darling.backorder metafield (product-level) → the storefront renders the "по поръчка · по-дълга
    # доставка" badge off it. CONTINUE (sale_below_zero=YES) products are backorderable; others are not.
    s.gql("""mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ userErrors{ message } } }""",
          {"m": [{"ownerId": pid, "namespace": "darling", "key": "backorder", "type": "boolean",
                  "value": "true" if policy == "CONTINUE" else "false"}]})
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
                      "fulfillments/create", "customers/create", "customers/update",
                      # carts view ("Кошници") — panel records these into a read-only carts table;
                      # HMAC-verified, never touches Barsy. (panel checkouts webhook, 2026-07-27)
                      "checkouts/create", "checkouts/update"]
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
        bp_price = _price(art).strip()
        try:
            sp = s.gql("query($id:ID!){ productVariant(id:$id){ price } }", {"id": vid})
            sh_price = str(sp["productVariant"]["price"] or "").strip()
        except Exception:
            continue
        if bp_price and bp_price != sh_price:
            drift.append({"code": code, "barsy": bp_price, "shopify": sh_price})
            if do_write:
                barsy_article_to_shopify(code, do_write=True, force=True)   # bypass echo guard (audit fix)
    return {"checked": min(limit or len(cat), len(cat)), "drift": drift, "fixed": do_write}


def backfill_variant_weights(limit=None, do_write=False):
    """One-off/periodic: set the Shopify variant weight from Barsy `extra_data.weight_kg` for products
    that lack it or drifted. The regular push now sets weight on create/update, but existing draft
    variants created BEFORE weight was wired need this backfill (so courier /quote gets real weights
    without waiting for a price/stock change to trigger a push). Skips products with no Barsy weight —
    never writes 0 (unset > weightless). Returns the change list; do_write applies it."""
    cat = _catalog()
    changed, skipped_noweight = [], 0
    for code, art in list(cat.items())[:(limit or len(cat))]:
        w = sync._parse_weight_kg((art.get("extra_data") or {}).get("weight_kg"))
        if w is None:
            skipped_noweight += 1
            continue
        pid, vid = _find_shopify(code)
        if not vid:
            continue
        try:
            cur = s.gql("""query($id:ID!){ productVariant(id:$id){
                        inventoryItem{ measurement{ weight{ value unit } } } } }""", {"id": vid})
            m = (((cur.get("productVariant") or {}).get("inventoryItem") or {}).get("measurement") or {}).get("weight")
            cur_val = (m or {}).get("value")
        except Exception:
            cur_val = None
        if cur_val is not None and abs(float(cur_val) - w) < 1e-6:
            continue                                    # already correct — skip
        changed.append({"code": code, "weight_kg": w, "was": cur_val})
        if do_write:
            s.gql("""mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(
                     productId:$pid, variants:$v){ userErrors{ field message } } }""",
                  {"pid": pid, "v": [{"id": vid,
                   "inventoryItem": {"measurement": {"weight": {"value": w, "unit": "KILOGRAMS"}}}}]})
    return {"checked": min(limit or len(cat), len(cat)), "changed": changed,
            "skipped_no_weight": skipped_noweight, "written": do_write}


# ---- Barsy webhook registration ----
BARSY_EVENTS = {"articles": 1, "articles_availability": 9, "categories": 6,
                "accounts": 2, "clientorders": 3, "clients": 4}


def subscribe_barsy_webhook(event_name, receiver_url):
    et_id = BARSY_EVENTS.get(event_name)
    if not et_id:
        return {"error": f"unknown event {event_name}", "known": list(BARSY_EVENTS)}
    return b.call("Webhooks_subscribe", {"event_type_id": et_id, "url": receiver_url})
