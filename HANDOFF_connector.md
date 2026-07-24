# Handoff → admin-panel agent: bidirectional Barsy ⇄ Shopify webhook service

The **connector logic is built** in `../agent-pipeline/barsy_shopify_connector.py` (part of the
shared business-core — import it, don't reimplement). Your job is the **HTTP hosting layer**: two
webhook receiver endpoints in the panel's FastAPI backend, webhook registration, and signature
verification. This is not urgent — it's needed when the store takes **real orders**, not for the
draft catalog. Do it after the backend is on an always-on, public-HTTPS server (webhooks can't
reach the laptop).

## Why this shape
Neither Barsy nor Shopify talks to the other, and neither can connect to an external DB — both are
closed SaaS exposing only APIs + webhooks. So the integration is a connector in the middle:
`Barsy ⇄ [receiver + API caller] ⇄ Shopify`. There is **no native Barsy–Shopify app** (Barsy has
native WooCommerce only); confirm with Barsy support if you want, else this custom connector is it.

## FULL function set now built (2026-07-20) — `barsy_shopify_connector.py`
All dry-run validated; every write is `do_write=False` by default and marked VERIFY-LIVE where it
mutates sales/stock. **Shopify → Barsy:** `shopify_order_to_barsy(order, mode='sale'|'clientorder')`,
`shopify_order_cancellation_to_barsy`, `shopify_refund_to_barsy`, `shopify_fulfillment_to_barsy`
(tracking #), `shopify_customer_to_barsy` (Clients_createsmart), `shopify_product_edit_to_barsy`
(OPT-IN, off by default, echo-guarded). **Barsy → Shopify:** `barsy_new_product_to_shopify`,
`barsy_article_to_shopify`, `barsy_quantity_change_to_shopify` (stock), `barsy_deleted_product_to_shopify`
(archives), `barsy_category_change_to_shopify`, `barsy_image_change_to_shopify` (staged-upload replace).
**Infra:** `register_all_webhooks(receiver_base)`, `reconcile(do_write)` (nightly drift sweep — RUN IT;
webhooks get missed), `mark_pushed`, `_echo` (content-hash loop-guard).

**Stock display (owner decided):** show LIVE stock — variants become `tracked:true`, policy `DENY`
(sold-out at 0). The buyable-at-0 exception is **Barsy-driven, not a heuristic**: Barsy's per-article
field **`sale_below_zero`** (YES/NO/DEF) is the source of truth → connector maps YES→`CONTINUE`,
else→`DENY`. Mira-N products get `sale_below_zero=YES` written into Barsy at push time (barsy_push.build).
So to change a product's oversell behaviour, edit it IN BARSY. ⚠️ Don't flip tracking on globally until
real stock quantities are loaded (barsy_stock.py + the наличности export) — else everything shows sold-out.

**Loop safety:** `_echo(code, content)` fingerprints the last synced name/price; a webhook whose value
matches is skipped, so Barsy↔Shopify edits converge instead of ping-ponging. Call `mark_pushed(code)`
after each `approved_pipeline` push so our own Barsy writes don't echo to Shopify.

Legacy 2-function import (still valid):
```python
from barsy_shopify_connector import shopify_order_to_barsy, barsy_article_to_shopify, mark_pushed
```
- **A. `shopify_order_to_barsy(order_payload, do_write=False)`** — maps a Shopify order → a Barsy
  `Accounts_place` sale that logs the order and **draws down stock**. Idempotent by `uuid =
  shopify-<order_id>` (retried webhook never double-sells) + a local `shopify_orders_synced.jsonl`
  guard. Returns a plan dict; pass `do_write=True` to write. Line items are matched to Barsy by
  **SKU == our product code**; unmatched SKUs are returned in `missing_skus` (don't silently drop —
  surface them).
- **B. `barsy_article_to_shopify(code, do_write=False)`** — a product changed in Barsy → mirror
  name/price onto the matching Shopify product (found by SKU). Skips products not yet on Shopify and
  our own echoes (loop-guard). Returns a plan dict.

## Endpoints to build (panel backend)
1. `POST /webhooks/shopify/orders` → verify Shopify HMAC (`X-Shopify-Hmac-Sha256` against the app
   secret), then `shopify_order_to_barsy(request.json, do_write=True)`. Return 200 fast (queue the
   work if it's slow — Shopify retries on non-2xx). Subscribe to `orders/paid` (paid orders only)
   or `orders/create` via Shopify `webhookSubscriptionCreate`.
2. `POST /webhooks/barsy/article` → the payload carries the changed article; call
   `barsy_article_to_shopify(code, do_write=True)`. Register it once with
   `subscribe_barsy_article_webhook("https://<panel-host>/webhooks/barsy/article")` — Barsy sends a
   test call on registration and needs a 2xx/401/403/405/500/501 reply to validate.

## Two things you MUST handle
- **Fiscal model — DECIDED (owner 2026-07-20): Barsy IS the fiscal point.** So closing the Barsy
  sale (`Accounts_place`) SHOULD print the fiscal receipt and draw down stock — that's intended.
  Prereq: a **service POS/каса with a fiscal device** must be configured for order reception (this is
  how Barsy's own e-shop integrations route online orders — a service каса per trading facility).
  Two ingestion modes to expose (mirror Barsy's WooCommerce integration): `mode='sale'` = finalize
  immediately (auto-process), or `mode='clientorder'` = log as a client-order for staff review, then
  `Accounts_createfromclientorder` + close on fulfillment. Default to whichever the owner prefers per
  workflow (review-first is safer for stock accuracy).
- **Loop guard.** Our `approved_pipeline` also writes Barsy (from our DB), which will fire
  `article.updated` and bounce back to Shopify. Prevent the loop: call
  `barsy_shopify_connector.mark_pushed(code)` right after each `barsy_push.push_one` in
  `approved_pipeline.run()` — Direction B already skips codes marked within 120s. (Add this when you
  turn the Barsy webhook on; it's a 1-line insert I left out of the live push for now.)

## Source-of-truth note
Today: our enriched DB → Barsy + Shopify (one-way). Once staff edit products **in Barsy**, Barsy
becomes the operational source for those; Direction B keeps Shopify in sync. Decide the policy so the
DB→Barsy push doesn't overwrite Barsy-side edits (e.g. stop pushing already-launched products from the
DB, or only push new ones).

## Validated so far (dry-run, no writes)
Order with `sku=0151086,qty=2` → maps to Barsy `article_id=724`, qty 2, `uuid=shopify-<id>`, payment
method resolved; bad SKU flagged. Barsy→Shopify found `gid://…/11452186100053` and read the title.
The write paths (`Accounts_place`, `productUpdate`) are untested-live by design.
