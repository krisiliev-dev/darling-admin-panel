# #82 Clientorders — design + plan (2026-08-04)

**Status of this doc:** research/design only. No code changed. Read this before touching the order
path; the headline finding rewrites what „#82 rewire" means.

## TL;DR — the write path already exists, is idempotent, and is gated off for launch

The parity queue carried #82 as „Clientorders rewire — day's work; the panel maps orders onto the
wrong entity." That framing is **outdated**. The full Woo → Barsy clientorder → sale → ship/refund
path is already implemented in the connector (`darling_core/_vendor/barsy_shopify_connector.py`)
and orchestrated by `routers/webhooks.py` + `routers/orders.py`. It is idempotent, it routes every
write through the connector's identity (which passes the registered-каса gate), and it is
deliberately **gated OFF** until launch. There is nothing to „rewire." The remaining work is
(a) a staging end-to-end verification with the gates ON, and (b) one small optional read-only
enhancement. The real launch-blocker is #87 (the fiscal device), not this code.

Do NOT rewrite the connector order flow. It encodes hard-won money-correctness (see the audit-fix
comments dated 2026-07-28 and the #1003 double-clientorder race fix).

## The wiring as it actually is today

Ingest (Shopify → Barsy), `webhooks.py`:
- `orders/paid | orders/create | orders/updated` → `connector.shopify_order_to_barsy(payload,
  mode=order_ingest_mode, do_write=webhooks_live)`. With `order_ingest_mode="clientorder"` (the
  default, and the owner's review-first model) this logs a Barsy **CLIENTORDER** — no stock
  decrement — via `Clientorders_create {order, rows}` (connector ~L506). `mode="sale"` would
  finalise immediately via `Accounts_place`; we are on clientorder.
- `orders/cancelled` → `shopify_order_cancellation_to_barsy`; `refunds/create` →
  `shopify_refund_to_barsy`; `fulfillments/*` → `shopify_fulfillment_to_barsy`; `customers/*` →
  `shopify_customer_to_barsy`.

Review → finalise / refuse, `orders.py`:
- The sanctioned finalise path is **Етикет** (`POST /{id}/label`) — finalising and labelling are one
  packing action. `POST /{id}/finalise` is MANUAL RECOVERY only (repair a half-finished pack).
- Finalise → `connector.finalise_order(numeric_oid, do_write)` →
  `Accounts_createfromclientorder` with `flag_close_account:1`, `print_receipt:1`, payment under
  „Наложен платеж" (**paymethod_id 6**): closes the account, DECREMENTS stock, and PRINTS A REAL
  FISCAL БОН (paid-at-packing model).
- Refuse → `connector.refuse_order`. Still-clientorder → VOID via
  `Clientorders_setstatus {client_order_id, status_id:6}` (6 = Отказана; nothing decremented).
  Already-finalised sale → STORNO (needs the fiscal УНП — see #85a/#73).
- Ship → `connector.ship_order(numeric_oid, tracking, do_write)`; `tracking_number` is written
  INSIDE the Barsy account's `account_props` (an AccountInputData field), and the panel also joins
  its own courier waybill by `order_id` (Barsy's `tracking_number` isn't the courier match key).

Order LIST, `orders.py :132` `GET /api/orders`:
- Sourced from **Shopify** (`shopify_orders.fetch_orders`) merged with the panel's local
  op-status (`state_map`: status, courier, tracking, label_ref). It does NOT read Barsy
  clientorders. The finalise/refuse/ship actions operate on the Barsy side by `order_id` join, so
  the list not showing the Barsy clientorder id/account is a display gap, not a correctness gap.

## Idempotency — the double-бон guard (as built; assessment: sound)

Layered, with Barsy itself as the durable source of truth:
1. **Same-order in-process lock** (`webhooks.py :44`): Shopify fires `orders/create` AND
   `orders/updated` for one order almost simultaneously; without serialising, both pass the
   `_seen_order` dedup before either writes state and BOTH book (the #1003 double-clientorder).
   Tasks keyed by payload id are serialised, closing the race.
2. **Local ledger** `ORDERS_STATE.jsonl` (`_seen_order` / `_record_order`, self-invalidating
   cache): the latest record per order; a finalise appends `kind='sale'` overriding the original
   `kind='clientorder'` so a later cancel hits storno, not clientorder-void. `account_id` always
   stays the sale/clientorder account; a storno records `storno_account_id` via extra, never
   overwriting.
3. **Durable Barsy source** `_barsy_account_for_clientorder(cid)`: asks Barsy „does client_order N
   already have a sale account covering the amount?" — Barsy's own rule
   („по клиентска заявка N вече има създадена сметка") is what really blocks a double-decrement, and
   it survives a container recreate wiping `ORDERS_STATE.jsonl`. A storno'd account still counts.
4. **already_finalised backstop** (`orders.py :174`): after a connector restart a second
   Финализирай re-calls `Accounts_createfromclientorder`; Barsy 501 „вече има" is treated as
   idempotent success, nothing decremented twice.

This is the „claim atomically, never check-then-act, a miss = two Barsy sales / two бона" rule
already satisfied — the ultimate claim is Barsy's, not a local check. No redesign needed.

## каша-gate routing — already correct

Client-order / payment / sale writes from an unregistered browser are refused by Barsy
(„изисква регистрирана каса… Darling7 ID:1"). Every order write here goes through the **connector's**
identity (`b.call` inside `barsy_shopify_connector.py`), which already passes the gate — not a fresh
panel session. So the standing rule „panel order-writes must go through the connector identity" is
satisfied by construction. (Client CRUD in #79 is different: it is NOT каша-gated and writes
directly.)

## The gates — everything is OFF pre-launch, by design

`config.py`: `webhooks_live=False`, `barsy_write_live=False`, `cod_barsy_post_live=False`,
`order_ingest_mode="clientorder"`. Reads are never gated; every Barsy-writing order path is a
dry-run until these flip. They are meant to flip at launch, AFTER the fiscal POS is attached (#87).
So the order path is fully built and sitting behind switches, not missing.

## Genuine remaining gaps (small — verify, don't assume)

1. **Staging E2E verification with gates ON** — the real work. See the plan below. Until a real
   Woo checkout has been driven through create → finalise → refuse → ship on staging with
   `webhooks_live`/`barsy_write_live` on, we have code-correctness but not result-correctness
   („test the RESULT, never the code" — a location must be attached).
2. **(Optional, low priority) Surface Barsy clientorder state in Онлайн → Поръчки.** Today the list
   is Shopify + local op-status. A read-only column showing the Barsy clientorder id / account /
   status (from `_seen_order` or a light Barsy read) would let staff see the ERP truth next to the
   Shopify row. This is the survey's „Clientorders as the middle layer" — but as a *display*
   enhancement, not a rewrite. Decide with the owner; it is not a launch-blocker.
3. **`Accounts_createfromclientorder` vs the survey's `Clientorders_create_accounts`** — the
   connector's choice is deliberate and correct (it closes + pays + fiscalises in the finalise
   step). No change; documented here so the survey suggestion isn't re-opened as a „gap."
4. **Delivery calendar (`Clientorders_getDeliveries`)** — unused; the panel uses its own courier
   integrations. Out of scope unless the owner wants Barsy's delivery calendar surfaced.

## Test plan (when/if we implement #2, and for the verification in #1)

- Unit (backend, mock `barsy.call` via the `barsy_recorder` fixture, no live ERP): assert the
  bodies — `Clientorders_create` at ingest (mode=clientorder), `Accounts_createfromclientorder` at
  finalise (flag_close_account/print_receipt/paymethod 6), `Clientorders_setstatus`→6 on refuse,
  `tracking_number` inside account_props on ship. Assert the same-order lock: two near-simultaneous
  `orders/create`+`orders/updated` for one id produce exactly ONE `Clientorders_create`.
- Idempotency test: a second finalise on an already-finalised order asserts `already_finalised`
  and zero extra decrement; `_barsy_account_for_clientorder` short-circuits when Barsy already has
  the account.
- Gate: ruff + mypy + the targeted order/webhook test files (`test_*order*`, `test_*webhook*`,
  `test_cod*`), then the full backend suite as a detached backstop (~15 min).

## Staging verification steps (the real #82 work — needs the owner/staging)

1. Deploy to staging: `gh workflow run deploy.yml --ref main` (CRLF: deploy only via
   `agent-pipeline/deploy_to_box.sh` — strips CR, verifies sha256).
2. On staging, flip the gates ON for the test window: `WEBHOOKS_LIVE=1`, `BARSY_WRITE_LIVE=1`
   (COURIER separately; `COD_BARSY_POST_LIVE` for the COD leg). Credentials are set on the box by
   the owner — not in chat.
3. Drive a real Woo checkout (Playwright): tests need `STAGING_PASS` and BG province **BG-04**, or
   shipping rates come back 0. Two staging gates: nginx user `darling`, WP user `darling_admin`.
   **Never `networkidle` on `/checkout/`** (Stripe iframes never idle).
4. Assert on the Barsy side (system of record): exactly ONE clientorder created (not two — the
   #1003 race); finalise → exactly one sale account + one fiscal бон, stock decremented once;
   refuse a second test order → clientorder voided (status 6), nothing decremented; ship → tracking
   on the account. Watch staleness, not just errors (a dead poller looks like a quiet day).
5. Because this fiscalises real бонове on the device, it is gated behind #87 (fiscal device attached)
   and is an owner-supervised action.

## Recommendation

Reclassify #82: it is NOT a rewrite. The write path is built, idempotent, and каша-safe. Split the
remaining work into:
- **#82a (owner + staging, launch-gated):** the staging E2E verification above. Blocked on #87
  (fiscal device) and the gate flip. This is the true „money path" step and must be human-driven.
- **#82b (optional, small, any time):** surface Barsy clientorder state read-only in the Поръчки
  list (survey's middle-layer idea, as a display column). Owner decides priority.

Next queue task for autonomous work is therefore better spent on **#70** (shared ProductList +
wrench column picker) or **#81** (Заявки lifecycle), which are self-contained and not gated on the
fiscal device — while #82a waits for launch conditions and owner review.
