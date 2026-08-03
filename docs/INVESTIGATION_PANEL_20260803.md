# Admin-panel investigation — 2026-08-03

Scope: read-only. Code on the laptop at `C:\Users\lora_\darling\admin-panel`; live box
`169.58.69.210` (panel) inspected over SSH read-only (`docker ps`, `crontab -l`, sqlite `mode=ro`,
one read-only Barsy `Articles_getlist`). No files changed, no git state changed, no Barsy writes.

---

## 1. Catalog ⇄ Barsy sync — the panel does NOT sync; the Каталог tab is a frozen snapshot

### Where the Каталог data comes from

- `frontend/src/pages/Products.tsx:23` — Продукти renders two tabs; `catalog` → `<Catalog embedded />`.
- `frontend/src/pages/Catalog.tsx:68` — the list is fetched from `GET /api/catalog/search`.
  (`:42` facets from `/api/catalog/facets`; `:279` detail from `/api/catalog/{id}`.)
- `backend/app/routers/catalog.py:278-333` `search_cache()` — queries **only** the SQLAlchemy model
  `ProductCache`. There is no Barsy call on this path.
- `backend/app/routers/catalog.py:125-127` says so explicitly:
  `# A SQLite mirror of Barsy's catalogue (product_cache) so search/filter is instant and doesn't hit
  # the low-tier Barsy per keystroke`
- `ProductCache` lives in **admin.db**, not pipeline.db:
  box path `/home/darling/product-review/admin-panel/backend/admin.db` (bind-mounted to `/app`).

Note: a *different*, genuinely-live endpoint exists — `catalog.py:782-805` `GET /api/catalog`
(`list_articles`) calls `barsy.call("Articles_getlist", …)` directly — but **no frontend page uses
it**. The Каталог tab is on the cached path.

`backend/app/routers/products.py` (`/api/products`) is a third, unrelated reader: it queries
**pipeline.db** (`dim_product`, `price_history`, `launch_readiness`) via `..core.db`. Also unused by
the Каталог tab, and pipeline.db on the box was last written **2026-07-23 11:00** (11 days stale).

### The sync job: exists, is manual-only, and has run exactly once

- `catalog.py:240-266` `POST /api/catalog/sync` — full `Articles_getlist` (paginated) + one
  `Store_amounts`, then **upsert**. Requires role `staff`.
- Its own docstring claims "manual trigger; **also run nightly**" (`:243`). That is false — there is
  no nightly runner:
  - grep across `backend/app/**` for `catalog/sync|sync_cache|scheduler|APScheduler|cron` → only the
    route definition itself and its audit line. No scheduler.
  - `crontab -l` on the panel box:
    ```
    15 3 * * * /home/darling/backup-darling.sh
    */5 * * * * /home/darling/peer-check-woo.sh >/dev/null 2>&1
    */10 * * * * /home/darling/taxonomy-watchdog.sh
    ```
    No catalog sync. `sudo crontab -l` → "no crontab for root". `/etc/cron.d/` → only distro files.
    `systemctl list-timers` → only distro timers.
  - The only UI trigger is on the **Ценообразуване** tab, not Каталог:
    `frontend/src/components/ProductBulkPricing.tsx:167` `api.post('/api/catalog/sync')`.

- Last run, from `admin.db.audit_log` (live query):
  ```
  (9, '2026-07-27 18:07:05', 'darling7.sv@gmail.com', 'catalog.sync', 'cached=39')
  ```
  That is the **only** `catalog.sync` row in the entire audit log (246 rows, 2026-07-24 → 2026-08-03).

- `product_cache` state (live query):
  ```
  product_cache count: 39
  updated_at min/max: ('2026-07-27 18:07:05.507303', '2026-07-27 18:07:05.507303')
  ```
  All 39 rows carry the same timestamp — never touched since.

### The upsert never deletes — this is the actual bug

`catalog.py:246-263`: it loads `existing` by `article_id`, then for each row **in the Barsy response**
updates-or-inserts. Rows present in the cache but **absent from Barsy are left untouched**. There is
no delete pass, no tombstone, no `updated_at < run_started` sweep.

Grep for any `ProductCache` deletion anywhere in `backend/app/**` → **zero matches**. Nothing in the
codebase can ever remove a cached product.

The write-through path has the same hole: `catalog.py:606-622` `refresh_cache_for_code()` (target of
the Barsy `article.updated` webhook, `routers/webhooks.py:301-306`) returns `False` and does nothing
when the article can't be found. And there is no `article.deleted` receiver — `webhooks.py:328` only
handles `POST /barsy/article` for updates.

### Proof that the 39 are the deleted products

Live Barsy, read-only `Articles_getlist` executed inside the panel container:
```
LIVE Barsy Articles_getlist rows: 2
ids: [16, 11365]          # 16 = "Авансово плащане", 11365 = "DELIVERY"
```
Barsy currently holds **2** articles. The panel shows **39**.

`/home/darling/barsy_wipe_backup.json` (written by the owner's wipe script):
```
ts 1785722154              # = 2026-08-03 04:55:54 UTC
count 40
ids [5127..5145, 5150..5153, 5158..5172, 11365, 11654]
```
Every `article_id` in `product_cache` except id 16 (5127–5172, 38 rows) is in that wipe list.
So: **38 of the 39 rows the owner sees were deleted in Barsy on 2026-08-03 04:56**; the 39th
(id 16, "Авансово плащане", empty code) is a Barsy system article that still exists. Conversely
article 11365 "DELIVERY", which is live in Barsy today, is **missing** from the panel — the cache is
stale in both directions.

### Is the connector process running?

There is **no separate connector process**. The Barsy⇄Shopify connector is a *library*
(`barsy_shopify_connector.py`) imported in-process by the panel (`backend/app/core.py:27`), and its
HTTP surface is `routers/webhooks.py` inside the same FastAPI app. `docker ps` on the panel box:
```
darling-panel|admin-panel-panel|Up 45 minutes
darling-caddy|caddy:2-alpine|Up 7 days
```
Both gates are ON in `/home/darling/.hermes/.env`: `WEBHOOKS_LIVE=1`, `BARSY_WRITE_LIVE=1`,
`COD_BARSY_POST_LIVE=1`, `DELIVERY_BARSY_LIVE=1`, `STORE_OPS_BARSY_LIVE=1`.
But nothing is arriving: the last webhook in `audit_log` is
`('2026-07-27 18:03:15', 'http:POST', '/api/webhooks/shopify/orders')` — six days before the wipe.
And even if Barsy fired an article webhook, it would not remove a deleted row (see above).

### Conclusion

**The panel does not sync with Barsy for the catalog.** The Каталог tab reads a one-shot snapshot
taken 2026-07-27 18:07 and never refreshed. Two independent defects:

1. **No refresh trigger.** No cron, no scheduler, no startup job. The only button lives on a
   different tab. Pressing it today would pull Barsy's 2 articles and *add* article 11365 — but
   would still leave the 38 deleted rows in place.
2. **Upsert-only sync.** `sync_cache` can never shrink. Deletions in Barsy are structurally
   invisible to the panel.

To make deletions reflect, all three are needed:
- add a delete/prune pass to `sync_cache` — capture the run's start timestamp, upsert, then delete
  `ProductCache` rows whose `updated_at` predates it (or diff the `article_id` set). Guard it: abort
  the prune if the Barsy fetch returned suspiciously few rows or raised mid-pagination, otherwise one
  bad Barsy page wipes the whole catalogue view.
- schedule it (a cron on the panel box calling the endpoint with a service token, or an in-app
  scheduler) — and surface `last_sync` where staff actually look, i.e. on the Каталог tab, not just
  Ценообразуване. Watch staleness, not errors.
- handle a Barsy `article.deleted` webhook (or make `refresh_cache_for_code` delete the row when the
  article is gone rather than returning `False`) so single deletions land immediately.

Immediate unblock for the owner: press **Продукти → Ценообразуване → синхронизирай** *after* the
prune is implemented. Pressing it now will not clear the 38 ghosts.

---

## 2. Проблеми page — connector/webhook anomalies; the 11 are 10 stale + 1 already-fixed

### What populates it

- Frontend `frontend/src/pages/Issues.tsx` → `GET /api/issues` (open first, then newest);
  `open_count` drives the nav badge and the Home strip (`backend/app/routers/summary.py:45`).
- Backend `backend/app/routers/issues.py` — read + `POST /api/issues/{id}/resolve` (acknowledge).
  Pure reads of the `ProcessingIssue` table; the router creates nothing.
- Rows are written by `backend/app/issues.py`:
  - `note_shopify_result(topic, payload, result)` — called from the Shopify webhook receiver
    (`routers/webhooks.py:229`) on every delivery. It inspects the connector's result dict and
    records: `unmatched_sku`, `total_mismatch`, `reversal_mismatch` / `not_finalised` /
    `reversal_unverifiable`, `invoice_not_issued`, `needs_review` / `error`, per-item `warnings`,
    `unhandled_topic`.
  - `note_exception(...)` — a handler threw → `kind="exception"`.
  - `record_issue(...)` from `backend/app/main.py:88-90` at startup for `source="ops"`,
    `kind="core_drift"`.
  - `Issues.tsx:22-25` documents a fourth writer: the server cron scripts
    (`/home/darling/ops-issue.sh`, used by `backup-darling.sh` and `peer-check-woo.sh`) write
    `backup_failed`, `backup_offsite_stale`, `woo_box_down` **straight into the table**, deliberately
    bypassing the API ("an alert that needs the panel to be alive is no alert").
- Dedup: `issues.py:36-50` — an unresolved row with the same `(source, kind, order_ref)` gets
  `count += 1` and a fresh `last_seen` instead of a new row.
- Resolution is manual acknowledgement only (`routers/issues.py:56-71`); nothing auto-resolves.

So the KINDS are: **order-booking / money-path anomalies from the Shopify→Barsy connector**, plus
**ops/infrastructure alerts**. Not panel HTTP errors, not data-quality issues.

### The actual 11 (live read of `processing_issue`, admin.db)

Total 11, **all open**, none ever resolved.

| id | created | last_seen | ×  | source | topic | ref | kind | gist |
|----|---------|-----------|----|--------|-------|-----|------|------|
| 11 | 07-31 14:55 | 07-31 20:14 | 9 | ops | deploy | — | core_drift | panel running `/app/agent-pipeline/db.py` (sha 8b5475017d0b) ≠ repo `_vendor` (944e0cd893e5) |
| 10 | 07-28 00:13 | same | 1 | shopify | fulfillments/create | #1006.1 | exception | `HTTP 501 on Accounts_place: Не съществува сметка №: 7` |
| 9  | 07-28 00:08 | same | 1 | shopify | refunds/create | #7718876512597 | error | `no_unp` (Accounts_storno, article 5128) |
| 8  | 07-27 21:30 | same | 1 | shopify | orders/cancelled | #1003 | exception | `Accounts_storno: "УНП на сторнираната бележка" е задължително` |
| 7  | 07-27 21:30 | same | 1 | shopify | refunds/create | 1081613058389 | exception | same УНП-required error |
| 6  | 07-27 21:30 | same | 1 | shopify | refunds/create | #7718604833109 | needs_review | `no_logged_sale` |
| 5  | 07-27 21:30 | same | 1 | shopify | orders/updated | #1002 | unmatched_sku | 1 line item with `(no sku)` |
| 4  | 07-27 21:30 | same | 1 | shopify | orders/cancelled | #1002 | needs_review | `no_logged_sale` |
| 3  | 07-27 21:30 | same | 1 | shopify | orders/updated | #1001 | unmatched_sku | 1 line item with `(no sku)` |
| 2  | 07-27 21:30 | same | 1 | shopify | orders/cancelled | #1001 | needs_review | `no_logged_sale` |
| 1  | 07-27 21:30 | same | 1 | shopify | refunds/create | #7718554599765 | needs_review | `no_logged_sale` |

### Still relevant?

- **ids 1–10: stale test noise.** Every one references Shopify test orders #1001–#1004 / #1006.1 and
  the 7718…/1081… test order ids — exactly the batch `admin-panel/LAUNCH_READINESS.md:109` lists for
  cleanup ("test orders #1001–#1004, dup clientorders ids 1/2, test client") and
  `admin-panel/docs/HANDOFF.md:265` records as the Barsy test residue. The business has moved off
  Shopify to WooCommerce, so these orders will never be re-fired and the errors cannot recur on that
  path. They can be marked resolved.
  Two of them do encode a **real, still-unfixed contract gap worth carrying forward** before the Woo
  connector goes live: `Accounts_storno` requires the `unp` of the reversed fiscal receipt (ids 7, 8)
  and `Accounts_place` needs a valid account id (id 10). Note them as tasks, then clear the rows.
- **id 11 (core_drift): real when raised, now fixed.** Live re-check inside the running container:
  ```
  core_drift NOW: []
  ```
  The container has been up 45 minutes and startup did **not** re-record it (`last_seen` still
  2026-07-31 20:14, count still 9). The drift is gone; the row is a leftover. Safe to resolve — and
  it is the one issue the owner should read the detail of first, because it is the class of failure
  described in `backend/app/core_drift.py:9-12` ("CI green + deploy green + health green + change
  absent").

Net: **0 of the 11 require action today.** All 11 can be acknowledged. That also clears the nav badge
so the next *real* issue is visible instead of buried in a permanently-red 11.

---

## 3. Settings → "Готовност за пускане (Shopify)" — obsolete as built, but 4 of its 8 checks are storefront-agnostic

### Where it is

- `frontend/src/pages/Settings.tsx:10` imports and `:70` renders `<PublishAudit />`.
- `frontend/src/components/PublishAudit.tsx` — heading literally `Готовност за пускане (Shopify)`,
  one "Провери" button → `GET /api/publish-audit`.
- `backend/app/routers/publish_audit.py` (#81). Read-only.

### What it checks

`publish_audit.py:33-72` enumerates **Shopify** sellable variants via
`connector.list_sellable_variants()` (defined at `connector/barsy_shopify_connector.py:1098`) and
scores each against the Barsy-code mirror (`ProductCache.code`) plus two registry snapshots:

| issue | source of truth | storefront-specific? |
|---|---|---|
| `no_sku` | variant has no SKU | **Shopify-shaped** (Woo calls it SKU too, but the enumeration is Shopify GraphQL) |
| `sku_not_in_barsy` | SKU ∉ `ProductCache.code` | **generic** — the Barsy join key; a null/unknown SKU means the order can't book, on ANY storefront |
| `zero_price` / `price_unknown` | `pricing_guard.gate()` + `brands_registry.zero_price_codes()` | **generic** — owner invariant 2026-08-02, fail-closed on money |
| `no_photo` | `brands_registry.image_codes()` | **generic** — owner invariant 2026-08-02, "sellable at the till, must not go online" |
| `not_tracked` | Shopify `inventory_management` | Shopify-specific concept (Woo: "Manage stock") |
| `not_active` | Shopify `product_status == ACTIVE` | Shopify-specific (Woo: publish status) |
| `policy_continue` | Shopify `inventoryPolicy` | Shopify-specific (Woo: backorders) |

Blockers: `publish_audit.py:20-21` — everything except `policy_continue`.

### Is it dead?

Effectively yes, today:
- The whole data source is Shopify. `SHOPIFY_STORE=r9et0w-1u.myshopify.com` is still set in
  `/home/darling/.hermes/.env`, so the call may still return the migrated (largely DRAFT) catalogue
   — but that catalogue is no longer the storefront being launched.
- The panel has **no WooCommerce integration at all**: grep for `woocommerce|woo_orders|WOO_` across
  `backend/app/**` → **zero matches**. (A `woo_orders.py` exists in `C:\Users\lora_\darling\connector\`
  but is not imported by the panel.) So there is nothing for a Woo version to read yet.
- The Barsy side it cross-checks (`ProductCache`) is the same 39-row frozen snapshot from §1, so
  `sku_not_in_barsy` would currently be wrong for essentially every variant.

### Recommendation: **adapt, don't delete — but park it behind the Woo work**

The valuable half is the *rules*, not the Shopify plumbing. Concretely:

1. **Keep** `_issues()`'s money and photo logic (`publish_audit.py:38-72`) and the
   `pricing_guard` / `brands_registry` integration — those encode owner invariants from 2026-08-02
   and their three-state "snapshot absent → cannot check, do not silently pass" contract is exactly
   right. Rewriting it later from memory would lose that.
2. **Replace** `connector.list_sellable_variants()` with a WooCommerce product enumerator once the
   Woo integration exists, and rename the section to "Готовност за пускане" (drop "(Shopify)").
   `no_sku` / `sku_not_in_barsy` / `zero_price` / `price_unknown` / `no_photo` carry over unchanged.
   `not_tracked` / `not_active` / `policy_continue` map onto Woo's manage-stock / publish-status /
   backorders.
3. **Do not** leave it rendering as-is on Настройки. Right now it either 502s ("Shopify недостъпен")
   or reports a confident audit of a storefront nobody is launching — a green tick from it would be
   actively misleading. Either hide the section until it is re-pointed, or make it fail loudly with
   "не е свързан с активния магазин".
4. Note the dependency: any Woo version is only truthful once `ProductCache` syncs properly (§1).
   Fixing #3 before #1 gives a wrong `sku_not_in_barsy` for 100% of products.

---

## 4. Заявки (supplier orders) — panel-local staging; the Barsy equivalent already exists in code but is unwired

### What the Заявки page does today

`backend/app/routers/zayavki.py:2-12` (docstring):
> Заявки — per-supplier purchase requests (outbound orders to suppliers). … **Panel-owned staging —
> Barsy records STOCK only at receiving (Доставки), so a заявка here is a purchasing document, not a
> Barsy write.** Data foundation: supplier_codes.jsonl … and the historical sales seed.

Flow (`frontend/src/pages/Zayavki.tsx`):
- insight cards (`/api/zayavki/insights`), suppliers list (`/api/zayavki/suppliers`),
  add line (`POST /api/zayavki/add`), qty ± / remove (`PATCH|DELETE /api/zayavki/lines/{id}`),
  notes+email (`PATCH /api/zayavki/zayavka/{id}`), send (`POST …/send-email`, Gmail SMTP),
  export (`GET …/export?fmt=`), history, per-supplier catalogue search.

Data sources:
- **admin.db tables `zayavki` + `zayavka_lines` + `supplier_prefs`** (panel-owned). Live counts on
  the box: `zayavki: 0`, `lines: 0`, `supplier_prefs: 0` — **the page has never been used in
  production.**
- **`backend/app/data/supplier_codes.jsonl`** via `backend/app/services/supplier_catalog.py` —
  on the box: 3,306,084 bytes, **22,807 rows across 35 suppliers**, mtime **2026-07-25 16:27**
  (top: MIRA-N 15,038; АНИС 1,680; 1,561 rows with a blank supplier; ЕГЕРА 1,098; ЛЕВЕНА 1,017).
  A static file, not a Barsy read.
- **`backend/app/services/product_seed.py`** (`data/seed_products.json`, 2026-07-25) for the
  insight cards' units/revenue/margin over a ~54-month window (`zayavki.py:37`).

Only Barsy touch in the whole router: `zayavki.py:260-290`
`POST /api/zayavki/supplier/{supplier}/push-barsy` → `Suppliers_update` if we know the Barsy
`supplier_id`, else `Suppliers_create`. That reflects the **supplier's identity**, not the order.
Owner-only and gated by `barsy_write_live()`.

### The Barsy equivalent — it exists, is documented, and is already implemented

**Barsy has no dedicated "заявка към доставчик" document type.** Confirmed against the mirrored
official docs at `C:\Users\lora_\darling\connector\barsy_docs\` and the Postman collection
(`BarsyApi.postman_collection.json`, 272 methods). The complete supplier-facing set is:

- `Storeloads_create`, `Storeloads_close`, `Storeloads_update`, `Storeloads_getlist`,
  `Storeloads_getdocumenttypes` (Postman folder «Склад / Складово зареждане»)
- `Suppliers_create`, `_update`, `_getlist`, `_index`, `_save_articles` (folder «Доставчици»)
- `Depots_getlist` (folder «Настройки / Складове»)
- `Reports_storeloads_details`

`Clientorders_*` is the **wrong direction** — the Postman folder is «Каса / **Клиентски** заявки»
(customer→us), and the connector uses it for Shopify order ingest
(`connector/barsy_shopify_connector.py:506` `Clientorders_create`). It is not a supplier order.

The заявка-shaped operation is a **draft storeload**. From
`connector/barsy_docs/methods/storeloads/create.rst`:
- Title: «Създаване на нова операция Зареждане от доставчик»
- Params: `$store_load` (StoreLoadInputData: `depot_id`, `supplier_id`, `doc_date`, `doc_num`
  (must be unique per обект), `doc_type_id`, `price_mode`, `has_tax`, `description`),
  `$articles` (StoreLoadInputRowData[]), `$action_type`.
- `$action_type`: `[save = Запис без приключване, save_and_close = Запис с приключване]`,
  **default `save`**. The docs state plainly that the document «може да бъде запазен в чернова
  (`save`) за по-късно дописване, или приключен веднага (`save_and_close`), при което количествата
  официално влизат в наличностите».
- Returns: `int` — id на създаденото зареждане.

So: **draft storeload (`action_type='save'`) = заявка; `Storeloads_close` = получена доставка.**

**This is already implemented in the panel** — `backend/app/routers/purchasing.py`, whose docstring
states the mapping verbatim:
```
• suppliers            → Suppliers_getlist
• product picker       → Articles_getlist
• Заявка / поръчка      → a DRAFT storeload (Storeloads_create action_type='save')
• получена доставка     → Storeloads_close (quantities enter stock)
• reorder suggestions  → Store_amounts (below minimum)
Nothing supplier/order-related is stored locally — only a panel audit entry (governance).
```
Routes: `GET /api/purchasing/suppliers|articles|reorder|orders`,
`POST /api/purchasing/orders` (`purchasing.py:126-160`, builds
`{"store_load": {...}, "articles": [...], "action_type": "save"|"save_and_close"}`),
`POST /api/purchasing/orders/{id}/close` (`:163-176`). Both writes are gated by `barsy_write_live()`
and return `{"dry_run": true, "gated": true}` when off. Registered in `main.py:171`.

**But it has no UI.** Grep for `/api/purchasing` across `frontend/src/**` → **zero matches**. The
Barsy-first supplier-order path is dead-ended in the backend while the panel-local Заявки page — the
one the owner sees — writes nothing to Barsy.

### Verification status of the contract

- **Documented (official Barsy docs, mirrored):** `Storeloads_create` params + `action_type`
  semantics + return value; `Storeloads_close`; `Storeloads_getlist`; `Suppliers_getlist/create/
  update`; `Depots_getlist`. Source: `connector/barsy_docs/methods/{storeloads,suppliers}/*.rst`.
- **Covered by our tests:** `Suppliers_create`/`_update` bodies
  (`backend/tests/test_zayavki.py:205-256`); the PO gate
  (`backend/tests/test_barsy_write_gate.py:85` `test_purchase_order_create_gated_writes_nothing`);
  `Suppliers_getlist` against live Barsy (`backend/tests/test_integration_live.py:94-97`).
- **NOT verified end-to-end against the live install:** no `Storeloads_create` /
  `Storeloads_close` call has ever been made against darling7.barsy.online from the panel. There is
  no audit row for `po.create` or `po.close` in `admin.db` (audit_log holds 246 rows, 2026-07-24 →
  2026-08-03; only `catalog.sync`, `brand.add/delete`, `auth.login` and HTTP traces). The field
  details the docs leave loose are therefore **unconfirmed**:
  - `doc_type_id` — `purchasing.py:130` hardcodes `1`; the real values come from
    `Storeloads_getdocumenttypes`, which we have never called.
  - `operation_type: 1` is sent at `purchasing.py:131` but is **not in the documented
    StoreLoadInputData** — unknown whether Barsy ignores it or 501s.
  - `doc_num` is documented as required-unique per обект and `purchasing.py` **never sends one** —
    a likely hard error or a silent auto-number on the first real call.
  - `depot_id` is discovered from `Depots_getlist` and falls back to `1`
    (`purchasing.py:29-38`) — that fallback is exactly the "defensive construct that degrades to a
    falsy-ish default" shape the project rules warn about; on a wrong depot the stock lands in the
    wrong place.
  - `price_mode` / `has_tax` are not sent at all — defaults (0 / 1) will apply, i.e. **prices
    treated as без ДДС and the document as с ДДС**. Unconfirmed against how the accountant expects
    delivery prices to be entered.

**What would need capturing from the Barsy admin UI** (per the project rule "docs incomplete;
capture admin-UI XHR, don't guess"): open Склад → Зареждане, create a заявка-style draft and then
close it, and record the network XHR for both steps — specifically the exact `doc_type_id` chosen by
the UI, whether `doc_num` is client- or server-generated, the `operation_type` field (if any), and
the `price_mode`/`has_tax` the UI sends for a normal supplier delivery. Also call
`Storeloads_getdocumenttypes` read-only to enumerate the legal `doc_type_id`s. Do this on a throwaway
draft and delete it — remember `_save` probes CREATE real records.

### Recommendation

Заявки and Доставки/purchasing are two implementations of one business process, split across a
panel-local store and an unwired Barsy path. The Barsy-first one matches the "Barsy is the system of
record" rule. Once the storeload contract is captured and verified, wire Заявки's "send" step to
`POST /api/purchasing/orders` (`action_type='save'`) so a заявка becomes a real Barsy draft
storeload, and let Доставки close it. Until then, be explicit in the UI that a заявка exists only in
the panel — currently nothing on the page says so.
