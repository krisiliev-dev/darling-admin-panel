# Barsy Reflection Audit — Дарлинг Admin Panel

**Scope:** Does every panel operation that should mutate the ERP system-of-record (Barsy) reach it, with a correctly-shaped call and the full field set? **Method:** Static trace of `admin-panel/backend/app/routers/*` against `agent-pipeline/barsy_api.py`, `barsy_push.py`, and the `agent-pipeline/barsy_docs` mirror (methods/objects `.rst` + `BarsyApi.postman_collection.json`). **Read-only review — no code changed. All write paths remain gated (`DELIVERY_BARSY_LIVE` dry-run pre-launch; owner-only + confirm on live writes).**

**Headline:** Every write operation *reaches* Barsy with a *correctly-shaped* call. The single systemic defect is that the **product create/update path writes a thinner article than the pipeline** — it silently drops **brand (Марка)** and **SEO/weight (`extra_data`)**, leaving those to be backfilled by a later `barsy_push` run. Bulk ops are correct in shape but have **no burst-throttle resilience** on the low tier. Order fulfilment does **not** write back to Barsy at all (delegated to the Shopify connector).

---

## 1. Verdict table

| Operation | Panel file | Reaches Barsy? | Barsy method | Correctness | Gap (short) |
|---|---|---|---|---|---|
| Product create (approve / push) | `routers/intake.py` `_build_barsy_article` → `Articles_create` (L730) / `Articles_update` (L416) | **yes** (gated) | `Articles_create` / `Articles_update` | **partial** | Drops brand, `extra_data` (SEO/weight), `cat_id`; dup details on re-edit |
| Product create in delivery commit | `routers/intake_delivery.py` `_new_article_body` → `Articles_create` (L471) | **yes** (gated) | `Articles_create` | **partial** | Same drops; `CommitLine` has **no brand field at all** |
| Goods-in for delivery commit | `routers/intake_delivery.py` `_storeload_payload` → `Storeloads_create` (L482) | **yes** | `Storeloads_create` | **correct** | — |
| Delivery batch auto-finalise (goods-in) | `routers/deliveries.py` `Storeloads_create` (L222, L702) | **yes** | `Storeloads_create` (`save_and_close`) | **correct** | — |
| Bulk price edit | `routers/catalog.py` `bulk_price` (L327–379) | **yes** (confirm) | chained `Articles_update` (+`Articles_get` baseline on relative ops) | **correct shape** | No pacing / 501 not retried; 2N N+1; long blocking request |
| Bulk rename | `routers/catalog.py` `bulk_rename` (L566–603) | **yes** (confirm) | chained `Articles_update` | **correct shape** | No pacing / 501 not retried |
| Timed promo create/delete | `routers/catalog.py` `create_promo`/`delete_promo` (L439–548) | **yes** | `Pricelists_edit` + `Pricelists_save_articles`; `Pricelists_index`/`Pricelists_delete` | **correct** | id resolved by name-scan (fragile); string `active_struct_id` |
| Single price apply (markup) | `routers/pricing.py` `/apply` (L212) | **yes** (owner+confirm) | `Articles_get` → `Articles_update` (`actual_price`) | **correct** | Same thin-article scope, but writes only price (safe) |
| Bulk repricing | `routers/pricing.py` (L316) | **yes** | chained `Articles_update` | **correct shape** | Same throttle exposure as catalog bulk |
| Supplier order / заявка | `routers/purchasing.py` L148 + `routers/orders.py` `create_order` L148 | **yes** (staff) | `Storeloads_create` (`save`=draft / `save_and_close`) | **correct** | — |
| Order fulfilment / label + stock | `routers/orders.py` (L185–291) | **no** | *(Shopify + Speedy/Econt only)* | **by design** | Stock decrement reflected to Barsy via the Shopify connector, **not** by the panel — see §2 G5 |
| Stock / inventory / dashboards / quality | `inventory.py`, `home.py`, `insights.py`, `quality.py`, `inventory_health.py` | read-only | `Store_amounts`, `Store_amounts_lots`, `Articles_getlist`, `Reports_*` | **correct** | — |

**Confirmed-correct fundamentals** (all write builders): `barcode` sent as a single **string** (not array → avoids the comma/500 gotcha); `is_public:1` + `is_for_sale:1` override the doc default of `0`; `amount_type_id` uses the verified `_UNIT_ID` map (`бр=1…`); `actual_price` sent as a string, staff/Microinvest-entered, never AI-touched (price policy); `Articles_update` puts `article_id` at the **top level** (matches `update.rst`, not the abbreviated Postman body); `Storeloads_create` header shape matches `storeloadinputdata.rst` (`depot_id`/`doc_date`/`doc_num`/`operation_type=1`/`doc_type_id=1`).

---

## 2. Confirmed gaps — most severe first

### G1 — Brand (Марка) is never written by the panel `[HIGH]`
**Where:** `intake.py` `_build_barsy_article` (L640–664) and `intake_delivery.py` `_new_article_body` (L335–352).
Both build `details[]` **only** from `attributes_json` / `ln.attributes`. The dedicated `brand` field is captured, persisted and echoed (models L329/L346; persist L458/L539; echo L369) but is **never mapped into the article**. Barsy has no native brand column — brand lives as a `details` row `{detail_name:'Марка'}` (confirmed: `objects/articledetailinputdata.rst`, `methods/articles/create.rst:411-416` — no native brand field). Today brand reaches Barsy **only** when `barsy_push.py` `_details()` (L76–89) later touches the code, prepending `{'detail_name':'Марка','detail_value':brand}`. A product created purely through the panel has **no brand in the ERP** until an unrelated pipeline run backfills it.

**Fix — `_build_barsy_article` (`d` is an `IntakeDraft`, has `.brand`):**
```python
details = art.get("details", [])          # currently only assigned inside `if attrs:`
if d.brand:
    details.insert(0, {"detail_name": "Марка", "detail_value": d.brand.strip()})
art["details"] = details
```
**Caveat for the delivery path:** `_new_article_body`'s param is a `CommitLine`, which (intake_delivery.py L308–323) has **no `brand` field** — the naive one-liner would raise `AttributeError`. Fixing the commit path requires **first adding a `brand` column to `CommitLine`** and the frontend commit payload, then applying the same insert.

### G2 — SEO meta + weight lost on panel create (`extra_data` never sent) `[HIGH]`
**Where:** same two builders; neither emits `extra_data`.
`barsy_push.py` (L170–183) packs the native `extra_data` object: `seo_title`, `seo_description`, `weight_kg`, `variant_codes`, ending `art["extra_data"] = extra` (L183). `extra_data` is a documented native field (`objects/articleinputdata.rst:373`, typed *object*). The panel **collects and persists** `meta_title`/`meta_description` (`models.py:54-55`; approve L461; PATCH L542-543) but they **die at the builder** — panel-created products carry no SEO in Barsy, the agreed single source of truth, until a `barsy_push` run overwrites them.

**Fix (apply to BOTH builders):**
```python
extra = {}
if d.meta_title:       extra["seo_title"]       = d.meta_title
if d.meta_description: extra["seo_description"] = d.meta_description
# weight_kg — see note
if extra: art["extra_data"] = extra
```
**Note:** weight is not merely dropped — the panel **never captures** it (`IntakeDraft` has only the two meta columns). Full parity with `barsy_push` requires adding a weight input/column upstream first.

### G3 — Duplicate `details` rows on re-edit `[MEDIUM]`
**Where:** `intake.py` update path (`_save_to_barsy`, L414–421).
On update the panel re-sends the full `details[]` with `detail_name`/`detail_value` but **no `detail_id`/`key_id`**. Per `articledetailinputdata.rst`, an empty `detail_id` means **"нов"** (new detail). Repeated saves of the same product therefore **accumulate duplicate** Материал/Цвят/Марка rows. `barsy_push` avoids this by only gap-filling on update.
**Fix:** on the update branch, fetch existing details (`Articledetails_get`) and pass their `detail_id`/`key_id`, or skip re-sending `details` unless changed.

### G4 — Bulk write bursts silently drop items on the low tier `[HIGH → operational]`
**Where:** `catalog.py` `bulk_price` (L354–379) and `bulk_rename` (L595–603); client `barsy_api.py` retry gate L79.
There is **zero pacing** between serial `Articles_update` calls (relative price ops do **2** calls/item: `Articles_get` L361 + `Articles_update` L372). The retry gate is `if ("зает" in detail or e.code in (502,503,504)) and attempt<3` — **HTTP 501 is absent**. Per Hermes/Barsy memory (and the `barsy_api.py:63-65` comment), 501 is exactly what the low tier throws on write bursts. A burst 501 raises immediately at L83, is caught by the loop's bare `except Exception` (L374/L599), and the item is dropped into `failed` with **no retry**. A large apply can partially fail with no self-healing. (Connection errors *are* retried at L84–88; this affects only the HTTP-status path. If the 501 body happens to carry the busy-text, it already retries — but that's not guaranteed.)
**Fix:** add 100–300 ms inter-request pacing in both loops; make **501 (and "зает")** a retryable condition in `barsy_api.call`; surface per-code failure reasons, not just codes.

### G5 — Order fulfilment does not decrement Barsy stock `[note — confirm ownership]`
**Where:** `orders.py` (L185–291) writes shipments/labels to Speedy/Econt and status to Shopify; it makes **no Barsy call**. This is by design — stock is reflected to Barsy via the `barsy_shopify_connector`, not the panel. Flagged so the audit is explicit: **if the connector is off, a fulfilled order never decrements Barsy stock.** Confirm the connector owns this and is live before launch.

### G6 — Panel-created articles are uncategorised in Barsy `[MEDIUM]`
`_build_barsy_article` intentionally omits `cat_id`/`master_cat_id` (comment L651-653: "shopify_category_id is NOT sent to Barsy"). `barsy_push.build()` **does** set both via `barsy_taxonomy.ensure_path` (L151-154). Panel- and pipeline-pushed products therefore diverge. **Fix:** either resolve the Shopify leaf → Barsy `cat_id` with the same helper and set `art['cat_id']=art['master_cat_id']=cat_id`, or document that Shopify owns taxonomy and stop relying on `barsy_push` to categorise.

### G7 — Promo id resolution is fragile `[LOW]`
`Pricelists_edit(action_type=save)` does not return the new id, so `create_promo` re-lists **all** pricelists (`Pricelists_index`) and matches by name, taking `max(pricelist_id)` on collision (L441). No unique-name guard → two same-named promos are ambiguous. Also `active_struct_id='eStructForm_1'` is a **string** where `edit.rst` types it *int* (works in the live pilot → install-specific; undocumented in the mirror). **Fix:** enforce a unique promo name (append UUID/timestamp) or capture the id from the edit response if the install returns it; comment the struct-id as install-specific.

---

## 3. Under-using Barsy — fields/endpoints we should adopt

The panel writes a **narrower** article and misses native capabilities the ERP already exposes:

| Field / endpoint | Doc evidence | Set by panel? | Set by `barsy_push`? | Recommendation |
|---|---|---|---|---|
| `extra_data` (`seo_title`/`seo_description`/`weight_kg`/`variant_codes`) | `articleinputdata.rst:373` | **no** | yes (L183) | Adopt — G2. This is the agreed SoR for SEO/weight. |
| `details[]` `Марка` (brand) | `articledetailinputdata.rst` | **no** | yes (L76-89) | Adopt — G1. |
| `cat_id` / `master_cat_id` | `articleinputdata.rst` | **no** | yes via `ensure_path` | Adopt or document — G6. |
| `article_aliases` (synonyms) | `articleinputdata.rst` | never | at create only (L155-157; dropped on update, per the "вече е прикачен" 501 gotcha) | Optional — improves POS/search. Set **only on create**. |
| `ref_num` (Каталожен номер) | `articleinputdata.rst` | **no** | at create (L209) | Set from supplier/catalog number on create — traceability. |
| `min_amount` / `max_amount` (+`_by_depots`) reorder thresholds | `articleinputdata.rst` | **no** | `min_amount` only | Wire so reorder suggestions live in the ERP, not just the panel. |
| `sale_below_zero` (oversell policy) | `articleinputdata.rst` | **no** (defaults `DEF`) | `YES` for made-to-order Mira-N | Expose a toggle only if the panel intakes made-to-order goods; else leave to `barsy_push`. |
| `slug` (public URL) | `articleinputdata.rst` | **no** | **no** | Correctly owned by Shopify — leave. |
| `Pricelists_save_article_patterns` (dynamic membership) | `methods/pricelists/*` | no (explicit `save_articles`) | n/a | Fine for explicit selections; note the pattern route exists for rule-based promos. |
| `custom_price` on promo rows | `save_articles.rst` (`PricelistArticleData`) | no (uses `modificator`) | n/a | An "exact price" promo mode could use `custom_price` instead of `modificator_type 5`. |
| **Native multi-row article write** | — | — | — | **Does not exist.** The postman mirror (272 methods) has zero bulk/mass/multi/batch write names; only `Articles_update` (single), `Articles_setcats`, `Articles_pricelists_save`, `Articles_save_recipe`. Chained single updates are genuinely the only option — see §4. |

---

## 4. Bulk-operations reality

**Native vs chained.** Confirmed against the docs mirror: **Barsy has no bulk/mass article-update method.** `methods/articles/index.rst` lists only single-item writes; the Postman collection has no `bulk`/`mass`/`multi`/`batch` method. So `bulk_price`/`bulk_rename` chaining one `Articles_update` per item is **the correct and only** approach for permanent name/price edits. The **promo** path, by contrast, *does* use native multi-row writes: one `Pricelists_edit` + one `Pricelists_save_articles({id, rows:[…]})` attaching all articles in a **single** call — no N+1 there (id looked up once via `Pricelists_index`). Promo field/shape verified against `pricelists/edit.rst` and `pricelists/save_articles.rst` (incl. `periods_date` `d.m-d.m` and the "rows OVERWRITES associations" note — safe for a fresh rule).

**Throughput on the low tier — the real risk.**
- One synchronous panel request can fire up to `_BULK_MAX = 2000` serially-awaited `Articles_update` calls — **≈4000 round-trips** for relative price ops (`Articles_get` baseline + update), each with up to 4 retries and a 60 s timeout. On the throttled low tier this single request can run for **many minutes**, risking Caddy/gateway timeout, and it is **not resumable**.
- Combined with **G4** (no pacing, 501 not retried), a large apply is the most likely place for silent partial failure in the whole system.

**Fixes:** (a) G4 pacing + 501 retry; (b) chunk the work / run as a background job with progress polling, or cap the synchronous batch well below 2000; (c) kill the **2N N+1** in relative price ops — batch-refresh baselines with one/few `Articles_getlist` pages by code/id before the write loop instead of one `Articles_get` per item (Barsy has no per-id batch get, but `getlist` returns many rows per page).

---

## 5. Prioritized wiring backlog

**S = small (one builder edit) · M = medium (client + loop changes / schema) · L = large (schema + frontend + job infra)**

| # | Item | Effort | Files | Why |
|---|---|---|---|---|
| P0 | **G1 brand → `details['Марка']`** in `_build_barsy_article` (init/assign `details` too) | **S** | `intake.py` | Panel becomes self-sufficient for brand; stop depending on `barsy_push` backfill. |
| P0 | **G2 `extra_data` (SEO)** in both builders | **S** | `intake.py`, `intake_delivery.py` | SEO is the ERP SoR; currently lost on panel create. |
| P0 | **G4 bulk resilience** — pacing + make 501/"зает" retryable | **M** | `catalog.py`, `pricing.py`, `barsy_api.py` | Stops silent partial failure of live bulk applies on the low tier. |
| P1 | **G1 brand on delivery commit** — add `brand` to `CommitLine` + payload, then insert | **M** | `intake_delivery.py`, models, frontend | Delivery-created products otherwise still brand-less. |
| P1 | **G3 dedupe details on update** — pass `detail_id`/`key_id` or skip unchanged | **M** | `intake.py` | Prevents duplicate detail rows accumulating on re-edit. |
| P1 | **G4b N+1** — batch baselines via `Articles_getlist` for relative price ops | **M** | `catalog.py` | ~halves round-trips on the slowest operation. |
| P1 | **Confirm G5** — verify Shopify connector decrements Barsy stock on fulfilment; document | **S** | (ops / `barsy_shopify_connector`) | Otherwise sold stock never leaves Barsy. |
| P2 | **G6 categories** — resolve leaf → `cat_id`/`master_cat_id` or document divergence | **M** | `intake.py`, `barsy_taxonomy` | Panel/pipeline taxonomy parity. |
| P2 | **G4c background job** — chunk bulk apply, progress polling, cap synchronous size | **L** | `catalog.py`, frontend | Removes multi-minute blocking request / timeout risk. |
| P3 | Adopt `ref_num`, `article_aliases` (create-only), `min/max_amount`, weight capture, `sale_below_zero` toggle | **S–M each** | `intake.py`, models, frontend | Close remaining thin-article gaps vs `barsy_push`. |
| P3 | **G7 promo** — unique-name guard / capture id; comment `active_struct_id` | **S** | `catalog.py` | Removes name-scan ambiguity. |

**Bottom line:** the panel's Barsy call *shapes* are correct and safe, and every mutating operation reaches the ERP. The gap is **completeness, not correctness** on the product path (P0 brand + SEO make the panel a faithful single write path) and **burst resilience** on bulk (P0 G4). Land those and the panel stops depending on a later `agent-pipeline` run to make products whole in Barsy.