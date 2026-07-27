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
- **Finalise** (`Accounts_createfromclientorder`, `flag_close_account:1`) → Barsy stock **9→8**, account
  closed, **fiscal:0 / no receipt** on the device-less POS.
- Webhook HMAC verified (200s), courier `/quote` returns real fees (econt 5.54€ for 1.8kg), stock sync
  is reservation-safe (pushes `on_hand`, never `available`).

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
- **Review → finalise / refuse buttons** on Онлайн поръчки. Contract (confirmed with connector):
  - *Finalise:* `Accounts_createfromclientorder(client_order_id, flag_close_account:1, print_shipment:0)`
    → then `record_finalised(order_id, new_account_id)` (so a later cancel storno's, not voids).
  - *Refuse:* cancel → the connector's cancel-branch voids the clientorder (no decrement, no fiscal).
  - This deploy also carries the 2 staged connector fixes (COD-client, `record_finalised`).
- **#81 — publish-readiness audit.** Per sellable variant, flag anything not launch-ready:
  **SKU = Barsy code · tracked=TRUE · policy=DENY (or CONTINUE if backorderable) · on_hand mirrored ·
  product ACTIVE.** This produces the exact "what to fix before publishing" list.
- **#83 — inventory reconciliation sequence.** `finalise → ship → [Shopify fulfilment + on_hand mirror]`
  so the Barsy decrement and Shopify `committed` never both reduce `available` (double-count guard). Not
  urgent (state is consistent today) but must land before live inventory sync.

### ⏳ Owner / hardware (only you can do these)
- **Connect a fiscal POS** (Barsy fiscal device). The storno (reversing a refused/returned finalised sale)
  needs a real fiscal receipt (УНП) to reverse; the test till is device-less so we couldn't live-prove it.
  The code path is built + will work once fiscal hardware is connected. *This is the one item deferred to
  hardware.*
- **Publish + stock-load the go-live product range** — run the stock-load per product (turns on
  tracking + anti-oversell) and set products ACTIVE. **#81 will hand you the exact list.**
- **Confirm courier / COD payout accounts** are contract-ready (Speedy + Econt are technically validated).

---

## 📋 Launch-day sequence
1. Clean the test data (test orders #1001–#1004, dup clientorders ids 1/2, test client) — Barsy UI cleanup.
2. Run the **stock-load per product** for the go-live range (flips tracked=TRUE + real on_hand + DENY/CONTINUE).
3. **Publish** those products (ACTIVE) on Shopify. Re-run **#81** → expect a clean audit.
4. Confirm the connector gates are production-set (WEBHOOKS_LIVE, BARSY_WRITE_LIVE — already on for testing).
5. Verify a real order end-to-end once with the fiscal POS connected (proves the deferred storno path live).
6. Go.

## Known constraints (by design, not gaps)
- COD orders do **not** issue a бон at order time — fiscal happens at delivery/finalise per the device model.
- Backorderable products (`sale_below_zero=YES`) intentionally allow overselling (Shopify CONTINUE); the
  anti-oversell block applies to `sale_below_zero=NO` (DENY) products.
