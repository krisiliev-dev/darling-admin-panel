# Дарлинг 7 — Launch readiness

Consolidated go-live reference, written after the **2026-07-28 end-to-end order-pipeline validation**.
Tick through the checklist at the bottom to launch. Status legend: ✅ done · 🔨 building · ⏳ waiting on
a person/hardware · 📋 launch-day step.

---

## Where we stand
The Shopify → Barsy order pipeline is **proven end-to-end** and the connector is **fully hardened**. In
one validation run we took it from *silent webhook 401s / nothing books* to a proven, observable flow:

**Proven with real orders (COD#1–#4):**
- Reserve-on-order: a Shopify order drops `available` (committed +1) — the storefront reservation.
- Books to Barsy as **exactly one** clientorder (double-book race fixed).
- Clientorder pending → **no** warehouse decrement (review-first model).
- **Етикет = the single pack action.** It finalises (`Accounts_createfromclientorder`,
  `flag_close_account:1`) → Barsy stock **9→8**, the account closes **PAID IN FULL** under the dedicated
  "Наложен платеж" method (`paymethod_id 6`) against the ANONYMOUS client with `print_receipt:1` (the
  fiscal бон fires here), then creates the courier label and marks the order shipped via a Shopify
  fulfilment. There is **no separate Финализирай button** any more.
  - On the device-less TEST POS nothing physically printed, so the early runs read as "no receipt" —
    that was the missing hardware, not the contract. The contract is бон-at-packing.
- Webhook HMAC verified (200s), courier `/quote` returns real fees (econt 5.54€ for 1.8kg), stock sync
  pushes `on_hand` (never `available`) so it *would* be reservation-safe — but the write itself is
  broken, see bug #5 below.

## Bugs found + fixed this session (the reason for the confidence)
All caught by the live test + the new **Проблеми** anomaly console (the console was the MVP of the run):

| # | Bug | Fix | Status |
|---|---|---|---|
| 1 | `.env` CRLF-poisoned secret → webhook 401s | strip `\r`, `dos2unix` the file (3 secrets were poisoned) | ✅ |
| 2 | Double-booking race (create+updated) | connector `_op_lock` + panel receiver per-order lock | ✅ live |
| 3 | Invisible order queue (`fetch_orders` double-unwrap → 0 orders) | remove the extra `.get("data")` | ✅ live |
| 4 | Anti-oversell off (`tracked=FALSE`) | per-product stock-load flips tracked+DENY+on_hand | 📋 launch step |
| 5 | Stock-sync write broken for 2026-07 Shopify API | `inventorySetQuantities(name:"on_hand") @idempotent + changeFromQuantity` | ✅ live |
| 6 | Storno missing required `unp` field → 501 | read the account УНП, pass in `storno_data`; no-unp → clean `needs_review` | ✅ live |
| 7 | Refuse-flow: cancel tried to storno a non-existent sale | cancel-branch: clientorder-stage → **void**; finalised → **storno** | ✅ live |
| 8 | Cancelled orders still shown in the fulfilment queue | `-status:cancelled` in `fetch_orders` | ✅ live |
| 9 | Order booked at Barsy price (42) not paid price (18.49) | carry Shopify line price in `current_price` **and** `original_current_price` | ✅ live |
| 10 | COD finalise on an anonymous client → 501 | `Clients_createsmart` a real Barsy client on intake | ✅ staged |
| 11 | deploy.sh didn't reload bind-mounted backend code | force-recreate the `panel` service after git reset | ✅ |

> ### ✅ bug #5 — CLOSED, and worth recording WHY the 2026-07-28 audit briefly re-opened it
> The audit flagged an inconsistency: the code sends `changeFromQuantity` while the comment above it
> says `compareQuantity`. That contradiction was real — but the audit guessed the wrong side was wrong.
> **Live introspection of `InventoryQuantityInput` shows its fields are exactly `inventoryItemId`,
> `locationId`, `quantity`, `changeFromQuantity` — there is no `compareQuantity` on that input**, and a
> live push was executed end-to-end (item 5131 `on_hand` −2→3, matching Barsy, `written:true`, no
> userErrors). So the CODE was right and the COMMENT was wrong; the comment has been corrected.
> **Lesson worth keeping:** a code-vs-comment contradiction proves an inconsistency, not which side is
> correct — settle it against the live API (introspection + a real call), never against prose.

## Panel features shipped this session
- **App logging → stdout** (webhook processing now visible in `docker logs`).
- **Проблеми** — connector-anomaly console: nav badge + Home strip + page; every refusal/failure surfaces
  with its reason instead of silently swallowing. `GET/POST /api/issues`.
- **Channel `/quote`** — token-gated live courier rate endpoint (weight+destination → fee).
- **e2e suite** grown to ~40 tests (auth, nav, access-control, online-pricing/Confirm, carts, home,
  intake, kancelaria, issues) + the `window.confirm`→in-app-modal sweep.

---

## Remaining work before launch

### 🔨 Panel builds (in progress — Claude/panel)
- **Етикет (pack) + Откажи** on Онлайн поръчки. ✅ **DONE + live-proven.** The finalise leg decremented
  Barsy 9→8 on #1004 and created the sale account from the clientorder. Re-firing after a container
  restart is **idempotent**: the connector's in-process guard is wiped by a redeploy, but Barsy's own 501
  ("вече има създадена сметка") blocks the second decrement, and the panel surfaces that as a calm
  "вече е финализирана (без промяна)" 200, not a 502 (commit 9caaf9f).
  - *Contract:* Етикет = `_finalise_for_pack` → `Accounts_createfromclientorder(flag_close_account:1,
    print_shipment:0, print_receipt:1, payments=[{paymethod_id:6, actual_paid_sum: total}])` →
    `record_finalised(order_id, account_id)` → courier label → `ship_order` (Shopify fulfilment).
    Откажи = connector cancel-branch voids the clientorder.
  - *No standalone Финализирай button exists in the UI.* `POST /orders/{id}/finalise` survives as an API
    (`backend/app/routers/orders.py:411`) for recovery/scripting; the operator flow is Етикет only.
  - *Connector durable fix (Hermes) — DONE + live:* `finalise_order`/`refuse_order` now reconcile against
    Barsy (system of record) so the idempotency guard survives a container restart. Panel backstop kept too.
- **Refuse — ✅ live-proven (#1005):** cancel Shopify order (reservation released) + void the clientorder
  (status 6), nothing decremented — the nekorekten fake-customer path. Barsy-confirmed.
- **Ship — ✅ live-proven (#1006):** `POST /orders/{id}/ship` → `connector.ship_order` fires
  `fulfillmentCreate` (SUCCESS, tracking attached), clearing `committed`; idempotent (2nd call = no open
  fulfilment order, no double-fulfil). Also auto-fires on label-success (non-fatal). **The live
  FulfillmentInput shape is confirmed.**
- **#81 — publish-readiness audit — ✅ done + live.** Ran against the store: 67 variants, 1 ready, 66 to fix
  (13 no-SKU · 65 untracked · 43 draft · 30 SKU-not-in-Barsy · 1 backorder). This IS the "publish + stock-load"
  worklist below. ⚠ Sanity-check the 30 "SKU-not-in-Barsy" for zero-padding vs genuinely-missing codes.
- **#83 — inventory reconciliation sequence.** Panel wiring `finalise → ship → fulfilment` DONE + proven; the
  `on_hand` mirror half is the connector's (Hermes). **OPEN:** decide whether the panel must block/warn
  Етикет on an un-finalised order (ship-before-finalise could let the on_hand mirror over-report). Awaiting
  Hermes's read on whether the mirror self-protects.

### ⏳ Owner / hardware (only you can do these)
- **Connect a fiscal POS** (Barsy fiscal device). The storno code path is **proven** — it reads all four
  fiscal fields (`unp`/`receipt_num`/`receipt_date`/`fm_num`) from the account and Barsy accepted a storno
  live (store_amount 8→9). What genuinely needs the fiscal POS is *upstream*: a real production бон must have
  been printed before there's a fiscal receipt to reverse — that's inherent to the business flow, not a code
  gap. Finalise already asks for the бон (`print_receipt:1`); the device-less test till simply had nothing
  to print on, so storno rides on whatever fiscal fields the account carries.
- **Publish + stock-load the go-live product range** — run the stock-load per product (turns on
  tracking + anti-oversell) and set products ACTIVE. **#81 will hand you the exact list.**
- **Confirm courier / COD payout accounts** are contract-ready (Speedy + Econt are technically validated).

---

## 📋 Launch-day sequence
1. Clean the test data (test orders #1001–#1004, dup clientorders ids 1/2, test client) — Barsy UI cleanup.
2. Run the **stock-load per product** for the go-live range (flips tracked=TRUE + real on_hand + DENY/CONTINUE).
2a. ✅ Prove ONE real Barsy→Shopify `on_hand` push right after the load (bug #5 is closed and was
   re-verified live on 2026-07-28) — flipping tracking on without a working push would freeze stock
   at the load value, so it stays a launch-day sanity check rather than a fix.
3. **Publish** those products (ACTIVE) on Shopify. Re-run **#81** → expect a clean audit.
4. Confirm the connector gates are production-set (WEBHOOKS_LIVE, BARSY_WRITE_LIVE — already on for testing).
4a. **Switch on `COURIER_LIVE`.** Real courier waybills are OFF by default — until this gate flips, Етикет
   returns a dry-run plan instead of creating a Speedy/Econt shipment, so nothing physically ships. Flip it
   alongside the other gates, and only after the Econt sender profile is configured (see
   WEBHOOKS_LAUNCH.md "Courier prerequisites"). ⚠ Once live, every Етикет costs a real waybill.
4b. **Set the фактура numbering (`min_inv_num`) to continue the Microinvest series — BEFORE any invoice is
   issued.** Barsy's counter currently starts at 0000000001; the first фактура issued consumes that number
   permanently, so the series must be set first or the books show a restart. ⚠ Do NOT create a test фактура
   until this is done. (Company ЕИК/БУЛСТАТ/МОЛ are already set — verified live.)
5. Verify a real order end-to-end once with the fiscal POS connected (Етикет → production бон + decrement →
   real waybill → Shopify fulfilment; then a storno on a real УНП).
6. Go.

### 🧾 B2B фактури — AUTOMATED (with an exception queue)
Issued automatically by the connector at finalise, via the **undocumented** `Invoices_save`. It is not in
Barsy's docs index (`methods/invoices/save.html` 404s) and blind probing only produced 500s — the contract
was obtained by driving Barsy's own admin UI with an XHR interceptor and capturing the real request.
Verified live end-to-end (invoice 0000000002, full receiver block, correct VAT split). Issuing is
**best-effort**: the sale and the бон always complete; an invoice failure never fails the order.

**Онлайн поръчки → Фактури** is therefore an **exception queue**, not the primary path — it lists only the
orders automation couldn't invoice (almost always incomplete company data), with the copy-ready company
block + the Barsy `account_id`, and records the number when staff issue one by hand. The storefront also
hard-gates Фирма+ЕИК+Град+Адрес when „фактура" is ticked, so exceptions should be rare.

> **Method lesson (cost us a wrong architecture once):** Barsy's docs are NOT a complete method list.
> `Invoices_save` existed all along. Probe/capture against the live system before concluding a capability
> is missing — but **never sweep `_save` methods blindly**: an empty `Accounts_save {}` body CREATED a real
> (empty) account. Use admin-UI capture to learn a payload, not trial-and-error POSTs.

## Known constraints (by design, not gaps)
- COD orders issue the **бон at packing**, not at delivery: Етикет closes the sale PAID under
  "Наложен платеж" with `print_receipt:1`. The accountant signed off on this (Н-18). The delivery-time
  correction is the **storno** path — an order the courier never collects sits falsely "paid" until
  reconciliation reverses it (`/api/cod/{id}/storno`). See COD_RECONCILE_DESIGN.md.
- Backorderable products (`sale_below_zero=YES`) intentionally allow overselling (Shopify CONTINUE); the
  anti-oversell block applies to `sale_below_zero=NO` (DENY) products.
