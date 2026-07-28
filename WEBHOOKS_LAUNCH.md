# Barsy⇄Shopify webhooks — hosting layer status & launch checklist

**Status (2026-07-21): HOSTING LAYER BUILT & TESTED, NOT LIVE.**
The receiver endpoints authenticate and observe, but write nothing until `WEBHOOKS_LIVE=1`.

## What's built (in the panel backend)
- `POST /webhooks/shopify/orders` — verifies Shopify HMAC-SHA256 on the raw body, dispatches by the
  `X-Shopify-Topic` header (orders paid/create/cancelled, refunds, fulfillments, customers), processes
  in a BackgroundTask, returns 200 fast. → `app/routers/webhooks.py`
- `POST /webhooks/barsy/article` — verifies a shared token (`?token=` or `X-Darling-Token`), extracts
  the article code, mirrors Barsy→Shopify. Token-valid probes with no code return 200 (registration).
- `POST /webhooks/reconcile` — owner-gated manual drift sweep (dry-run unless live).
- Master gate `webhooks_live` (default False): verified webhooks log a dry-run PLAN, write nothing.
- Connector bridged via `app/core.py` (`from ..core import connector`). 10 tests in `tests/test_webhooks.py`.
- Verified live: bad HMAC→401, bad token→401, no-auth reconcile→401, valid HMAC→200 (dry-run).
- **Phase-1 realistic-payload sim (2026-07-21):** POSTed a full Shopify `orders/paid` body (real SKU
  `201211`→Barsy art 450, a bogus SKU, COD/customer/address, `darling_*` note_attributes) with a
  correct HMAC, plus a Barsy article webhook with token. Signature/token accept+reject both correct;
  the connector ran against the **real Barsy catalog** and logged the right plan
  (`unmatched SKUs ['NOPE-9999']`, real SKU matched), writing nothing (`live:false`). This validates
  everything that is *our* logic. What's NOT yet tested is a payload delivered by Shopify/Barsy's own
  servers — that's Phase 2 below (needs a public URL; can't reach the laptop). Sim script:
  `scratchpad/wh_sim.py`.

## Phase 2 — real inbound delivery smoke test (do ONCE at launch, needs a public URL)
Local sim can't prove Shopify's *actual* payload shape/header casing or the *real* signing secret. Before
flipping live, run one genuine delivery — ngrok is optional (only if not yet deployed to the real host):
- If the backend is already on the public-HTTPS host (Launch step 1), use that host directly — **no ngrok**.
- Otherwise, temporarily: `ngrok http 8770` (or `cloudflared tunnel --url http://localhost:8770`) → public URL.
- Put the **real** Shopify signing secret in the host env (replaces the dev one), register the
  `orders/paid` webhook to `https://<url>/webhooks/shopify/orders`, place a test order, and confirm the
  receiver verifies with the real secret and logs the dry-run plan. Keep `WEBHOOKS_LIVE=0` for this —
  even the first real delivery should be an inspectable dry-run before step 8 flips the switch.

## ⚠ PRE-LAUNCH CLEANUP — remove ALL test data before go-live (do this FIRST)
During pre-launch we clean Barsy, load ~40 TEST products + test stock, and exercise every flow. All of
that MUST be wiped before the real catalog + real stock go in. Go through each and confirm clean:
- [ ] **Delete the test articles** (the 40 from `agent-pipeline/test_data_approved.jsonl`) and their stock.
- [ ] **Delete the TEST depot** "ТЕСТ — Склад 2 (изтрий преди старт)" (created only to exercise Прехвърляне).
- [ ] **Delete all test documents**: test storeloads/deliveries, storeouts (write-offs), storemoves
      (transfers), revisions (stocktakes), accounts/orders + payments, clientorders, clients, pricelists,
      any фактури — everything generated during system testing.
- [ ] **Clear test supplier↔article links** (`Suppliers_save_articles` empty rows) if any were set.
- [ ] **Reset idempotency/echo state** so test runs don't suppress real ones: connector order log
      (`_seen_*`/order-log JSONL), `.connector_echo.json`, `store_synced.jsonl`, `stock_loaded.jsonl`.
- [ ] **Confirm Barsy holds ZERO products/orders/stock** before importing the real catalog + real stock.
- [ ] Keep: suppliers, categories, users, POS/каса, payment methods, couriers, reasons, the MAIN depot.

## Launch steps (do these ONLY at store launch, in order)
1. **Deploy the backend to the always-on public-HTTPS host** (webhooks can't reach the laptop).
2. **Set secrets** on that host's env (NOT in git):
   - `SHOPIFY_WEBHOOK_SECRET` = the Shopify app's webhook signing secret.
   - `BARSY_WEBHOOK_TOKEN` = a long random value (e.g. `openssl rand -hex 32`).
   - `NEKOREKTEN_API_KEY` = the risky-customer lookup key (server env only, never git/browser).
   - Keep `COOKIE_SECURE` unset (defaults True) so session cookies are HTTPS-only.
2a. **Lock down the nekorekten API key** (currently open): in the nekorekten dashboard, change the IP
    allowlist from `0.0.0.0/0` (dev) to **this production host's static egress IP**, and **ROTATE the
    key** (the dev key was exposed in a chat transcript). A leaked key locked to one IP is useless
    elsewhere; an open key isn't.
3. **Configure the Barsy fiscal POS**: a service каса with a fiscal device for order reception
   (required — closing the sale fiscalises + draws down stock). `ORDER_INGEST_MODE` is **DECIDED:
   `clientorder`** (staff review first, safer for stock) — the order parks as a clientorder and the
   panel's Етикет action closes it. Don't switch to `sale` without redoing the COD model.
4. **Load real stock quantities** (barsy_stock.py + наличности export) BEFORE flipping tracking on,
   else every Shopify variant shows sold-out. Oversell is Barsy-driven per article via `sale_below_zero`
   (Mira-N = YES→CONTINUE); to change it, edit the product IN BARSY.
5. **Register webhooks**: register the 9 Shopify topics (orders/create, orders/paid, orders/cancelled,
   orders/updated, refunds/create, fulfillments/create, fulfillments/update, customers/create,
   customers/update) to `https://<host>/webhooks/shopify/orders` via `webhookSubscriptionCreate`, and
   the Barsy article URL via `connector.register_all_webhooks(...)` WITH `?token=<BARSY_WEBHOOK_TOKEN>`.
   ✅ DONE 2026-07-27 (dry-run): 9 Shopify topics registered. Barsy webhook BLOCKED — its probe can't
   reach the panel (cert/TLS/firewall; see the GDPR + Barsy notes below).
5a. **⚠ TODO — GDPR compliance webhooks (owner, Shopify Partner Dashboard):** the 3 mandatory privacy
    webhooks (`customers/data_request`, `customers/redact`, `shop/redact`) are NOT API-registrable —
    set their callback URLs in **Partner Dashboard → your app → Compliance webhooks** (or the privacy
    URIs in `shopify.app.toml`), all pointing at `https://<host>/webhooks/shopify/orders`. The panel
    handlers are DEPLOYED + tested (redact auto-erases our phone-keyed OrderOutcome + retracts nekorekten;
    data_request compiles + audits; shop/redact audits). INTERIM: the privacy policy directs data
    requests to our email, so we can run the erase/lookup manually (`/api/gdpr`) until the dashboard
    URLs are set. Setting them automates it.
6. **Add the loop guard**: insert `barsy_shopify_connector.mark_pushed(code)` right after each
   `barsy_push.push_one(...)` in `agent-pipeline/approved_pipeline.run()` (1 line — left out of the live
   push until the Barsy webhook is on, else Direction B could ping-pong our own DB→Barsy writes).
7. **Schedule the nightly reconcile** (webhooks get missed): a cron/scheduled task hitting the drift
   sweep. Keep it dry-run until step 8; it respects `webhooks_live`.
8. **Flip `WEBHOOKS_LIVE=1`** — only after 1–7. Watch logs for `missing_skus` (unmatched line items —
   these now REFUSE the sale + flag for review rather than booking a partial one) and mis-fiscalisation
   on the first real orders.
   - **G5 — verify stock decrement:** the live default is `ORDER_INGEST_MODE=clientorder`, so an order
     books as a Barsy CLIENTORDER and **nothing decrements at order time**. Stock drops at
     **finalise/pack** — the panel's Етикет action, which closes the sale
     (`Accounts_createfromclientorder`, `flag_close_account:1`) PAID under "Наложен платеж"
     (`paymethod_id 6`) with `print_receipt:1`. So on the FIRST real order confirm: (a) after checkout,
     Barsy stock is UNCHANGED and a clientorder exists; (b) after Етикет, stock drops by the ordered qty
     and the бон prints. If (a) already decrements, `mode='sale'` is wired by mistake — resolve before
     more orders.
9. **COD↔Barsy writes** are a SEPARATE gate: `COD_BARSY_POST_LIVE` (default off). It now gates
   **exactly one write — the STORNO**:
   - an uncollected/refused delivery (Разчети storno / risk `outcome=refused`) **stornos** the Barsy sale
     → reverses the paid-at-packing booking and returns the goods to stock.
   - a **settled/matched** COD payout writes **NOTHING** to Barsy. The sale was already booked PAID IN
     FULL at packing, so posting the collected money again would OVERPAY the account
     (`total_paid > total_sum`) and double-count the cash. Settle = CONFIRM ONLY (a `CodSettlement` row +
     status flip, panel-side). The old paymethod-5 payment post was deleted — do not reintroduce it.
   Trial the storno against a Barsy test object first, then flip `COD_BARSY_POST_LIVE=1`. It's idempotent
   (keyed in the connector's order log) so a repeated webhook/settle can't double-write.

## Source-of-truth policy to decide before launch
Once staff edit products IN Barsy, Barsy is the operational source for those. Stop the DB→Barsy push
from overwriting Barsy-side edits — e.g. only push NEW products from the enriched DB, not already-launched
ones. (See HANDOFF_connector.md "Source-of-truth note".)

## Courier prerequisites (before the first real label)
- **Configure the Econt SENDER profile** — Econt label creation currently FAILS because the account has
  no sender on file (`getClientProfiles` returns empty; `sender_configured:False`). Fix by setting the
  sender address in the Econt account profile, OR set `ECONT_SENDER_*` env (city/street/num/phone).
  Speedy has no such gap (its sender auto-discovers from `/client/contract`). See [[courier-integration]].
- **Verify the courier option field names cost-free** before trusting real labels: run Econt
  `mode:"validate"` (validates a label without creating it) and, for Speedy, create a real shipment then
  `cancel()` it before pickup (no charge — Speedy BG has no separate sandbox host). Confirm обявена
  стойност (declaredValue) + преглед (obpd/payAfterAccept) are accepted/applied. NOTE: `doc.speedy.io`
  is a *different* company (a payments platform), NOT the courier Спиди (`api.speedy.bg`) — ignore its
  sandbox. Do this once the Econt sender is configured.
