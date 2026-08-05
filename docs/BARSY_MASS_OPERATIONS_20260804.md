# Barsy „Масови операции" (article mass edit) — captured 2026-08-04

Owner asked to mirror Barsy's mass operations on the panel. This is what Barsy's article mass edit
actually is, the full field set it can change, the **exact `save_mass` wire body** (captured live from
the Barsy admin UI), and the recommended way to mirror it. Captured with three throwaway test articles
(30750, 30751, 30752) that were **created, probed, and deleted — verified gone, zero residue**.

## The mechanism: an editable grid + `save_mass`

Barsy's „Масови операции" on articles is NOT a set of discrete bulk dialogs — it's the **articles
list itself as a fully editable grid**. The list page is `articles_index` (aliases: `articles`,
`Articles_index`, `Articles`). Its one grid-level global action is:

```
save_mass  →  target {"articles_index": {"action_type": "save_mass", "active_struct_id": "eStructList_1"}}
           data_object: "values"   (no key_name/key_value)
```

You make the columns you want editable, type new values into the selected rows inline, and „Запиши"
(`save_mass`) persists them all in one call. There is no separate `Articles_massEdit` /
`Articles_massoperations` method — those 501 (don't exist).

## What's mass-editable — 142 of the grid's 185 columns

Essentially the WHOLE article record is mass-editable. Grouped (Barsy column name → what it is):

- **Price / costing:** `actual_price` (Цена продажна), `avg_delivery_price` (себестойност),
  `min_profit_percent`, `max_discount`, `user_prc`, `price_recalc`.
- **Identity / SEO:** `article_name`, `article_name_public`, `code`, `barcode`, `ref_num`, `ref_id`,
  `export_id`, `slug`, `description`, `intrastat`.
- **Units / packaging:** `amount_type_id` (Тип к-во, select ← Amounttypes_getList), `amount_unit`,
  `package_name` (разфасовка), `part_factor`, `supplements_type`, `process_time`.
- **Reorder / limits:** `min_order`, `max_order`, `min_amount` (мин. склад), `max_amount`.
- **Behaviour flags (checkboxes):** `is_for_sale_mass`, `is_for_load_mass`,
  `is_for_client_order_mass`, `is_for_store_move_mass`, `is_for_store_out_mass`,
  `is_for_irequest_mass`, `is_for_request_mass`, `is_service`, `is_public`.
- **Below-zero policies (selects):** `sale_below_zero`, `storemove_below_zero`,
  `storeout_below_zero`, `storeload_return_below_zero`.
- **Tax:** `tax` (ДДС група, select).
- **Suppliers:** `supplier_ids` (multiselect ← Suppliers_getList) — **mass supplier assignment**.
- **Characteristics:** `detail_0 … detail_N` — each maps to an ArticleDetails key
  (`ArticleDetails_getKeyValues {key_id}`), e.g. Бранд (key 4), material/type keys — **mass
  characteristic edit**.
- **Misc:** `stream_id` (Поток ← Streams_getList), `lot_type_id` (Партиди).

**Stock QUANTITY is NOT here.** `min_amount`/`max_amount` are reorder *limits*, not the on-hand
quantity. Changing actual stock is a Складови operation (storeload / adjustment), a separate
mechanism — not part of the article grid.

## The exact `save_mass` wire body — CAPTURED 2026-08-04 (live Chrome)

Captured the real UI request by driving Barsy's article grid in Chrome (throwaway article 30752,
„ТЕСТ-МАСОВИ-CAP", created → made „Цена продажба" column editable via Масови операции → Колони →
typed 12.34 in the € cell → Запиши → **deleted, verified gone**). The UI POSTs to
`/endpoints/json?bid=1` (session/cookie auth) with a **page-wrapped** body:

```json
{
  "articles_index": {
    "action_type": "save_mass",
    "active_struct_id": "eStructList_1",
    "values": {
      "articles_index_eStructList_1_30752_actual_price": {
        "row_id":       "articles_index_eStructList_1_30752",
        "col_name":     "actual_price",
        "value":        "12.34",
        "native_value": 9.99,
        "article_id":   "30752"
      }
    }
  }
}
```

Key facts, why the earlier connector guesses failed, and the contract:

- `values` is an **object, not a list** — keyed by `<struct>_<article_id>_<col_name>`
  (`articles_index_eStructList_1_<article_id>_<col_name>`). That is why `rows:[...]` gave HTTP 500 /
  no-op: the grid never reads a `rows` array.
- Each cell value carries: `row_id` (`<struct>_<article_id>`), `col_name`, `value` (the NEW value,
  as a string), `native_value` (the OLD value, for optimistic-concurrency / diffing), `article_id`.
- **Partial, not full-replace.** Only the cells the user actually edited are sent — untouched
  columns and articles are absent and left unchanged. (Contrast with page-form saves like
  `users_edit`, which are read-modify-write / blank-on-omit. The grid `save_mass` is a targeted
  cell-diff, so it is safe to send only the changed cells.)
- One call carries many cells across many articles: add one `values` entry per changed cell. Barsy
  owns the whole update as a single transaction.
- Auth/transport — **VERIFIED reachable from the connector** (2026-08-04, throwaway article 30755
  9.99→12.34, confirmed via `Articles_get`, deleted). The UI uses the adminx SPA shape
  (`/endpoints/json?bid=1`, body wrapped in the page id, cookie-authed), but the connector does
  **not** need cookies. This exact call works over Basic auth:

  ```python
  barsy.call("articles_index",
    {"action_type": "save_mass", "active_struct_id": "eStructList_1",
     "values": {  # one entry per changed cell, keyed <struct>_<article_id>_<col>
       "articles_index_eStructList_1_<aid>_actual_price": {
         "row_id": "articles_index_eStructList_1_<aid>", "col_name": "actual_price",
         "value": "12.34", "native_value": 9.99, "article_id": "<aid>"}}},
    bid=1)   # -> returns 1 on success
  ```

  i.e. `barsy.call` routes on the URL path (`/endpoints/json/articles_index?bid=1`) with the body
  UNWRAPPED — Barsy accepts that just as it accepts the UI's `?bid=1`-wrapped form. `bid=1` is
  REQUIRED (without it the call returns `1` but is a no-op). This is the path the panel uses.

## Decision (owner, 2026-08-04): build native `save_mass`, cover every field

Owner chose the **native `save_mass`** path (one Barsy call per apply, so Barsy owns the transaction)
over a per-article `Articles_update` loop, and asked for **all** of Barsy's mass operations on the
panel — price/costing, characteristics/units, supplier assignment, behaviour flags, "any and all
that Barsy supports". So the panel's Масови операции view builds the `values`-keyed-by-cell body
(above) from the selected products × chosen field(s) and posts the single verified call
`barsy.call("articles_index", {action_type:"save_mass", active_struct_id, values}, bid=1)`.

The per-article `Articles_update` loop below stays documented as the fallback (some fields — supplier
multiselect, `detail_N` characteristics — may need their own encoding inside a `values` cell; where
that proves fiddly, `Articles_update`/`Articles_save_suppliers`/`ArticleDetails_save` remain proven).

### Verified per-column semantics (throwaway articles, 2026-08-04, all deleted)

- **`native_value` is ignored.** A cell with a deliberately wrong `native_value` still applied — so the
  backend does NOT need to read current values first (except to *compute* a relative ×/÷/+/− delta).
- **Multi-type, multi-row** in one call: `actual_price` (currency), `article_name`/`code` (text),
  `is_public` (checkbox) all round-trip; two articles set in one `save_mass`.
- **Behaviour flags need the grid's `_mass` suffix** as `col_name`: `is_for_sale_mass` applies (sets
  `is_for_sale`); the bare `is_for_sale` does NOT. `is_public`/`is_service` have no suffix.
- **`min_amount` does NOT round-trip** via `save_mass` (set "5" → read back `null`) — on-hand min is
  per-depot (`min_amount_by_depots`). Reorder-limit columns need separate verification before use.
### Localized text (name / public name / description) — the i18n exception, verified end-to-end

Barsy stores names twice: the scalar `article_name` AND `article_name_ml`, a lang-keyed map
(`bg_BG`, `en_US`, …). The grid mass-edit only ever writes the **scalar**. Confirmed at every level
(throwaway articles, all deleted):

- **`save_mass` on `article_name` never touches the ml.** Verified 4 ways (plain, `+article_name_ml`
  string, `+`json, `+article_name_ml_bg_BG`) — the ml map is left byte-for-byte alone.
- **The real Barsy UI does the same.** Captured the live grid rename in Chrome: it POSTs
  `/endpoints/json?bid=1` with a single `article_name` cell and **no ml cell**. So the stale ml is
  Barsy's own designed grid behavior, not a connector omission.
- **Effect on a bilingual product** (bg_BG + en_US both set): after a grid rename, `en_US` is
  **preserved** but `article_name_ml.bg_BG` goes **stale** vs the new scalar.
- **`Articles_update` ml semantics** (also verified): a **partial** `{bg_BG: new}` body **REPLACES**
  the whole map → **drops `en_US`**. Only a **full** map `{bg_BG: new, en_US: kept}` preserves other
  locales while syncing bg_BG. A name-only `Articles_update` (no ml key) keeps `en_US` but leaves
  bg_BG stale (same as save_mass).

Consequence for i18n (the owner will read the ml for the English site): the only write that both syncs
the active-locale name AND preserves other locales is `Articles_update` with a **read-modify-write full
ml map**. `save_mass` cannot do it. So **localized-text fields stay on `Articles_update`, NOT the grid**:

| field group | write path |
|---|---|
| price / costing, flags, tax, units, reorder limits, code, barcode, slug | native `save_mass` |
| `article_name`, `article_name_public`, `description` (have `_ml` siblings) | `Articles_update` (full-map merge) |

`bulk_rename` is now **i18n-safe** (commit `7f79eea`): it reads each article's current
`article_name_ml`, merges the new name into the active locale, and writes `article_name` + the FULL
merged map — verified against live Barsy (en_US preserved, bg_BG synced). **Open item:** the
single-article editor `update_article` still writes partial `{bg_BG: …}` maps for `article_name_ml`,
`description_ml`, and `description_public_ml` (same latent en_US-drop) — its fix adds an `Articles_get`
that ripples into `test_catalog`'s exact-`operations` assertions, so it's queued separately.

Because columns are NOT uniform (min_amount, the ml, select/multiselect encodings), the panel's mass-op
catalog is a **verified whitelist**, expanded field-by-field as each is confirmed against Barsy —
never "trust all 142 blindly".

### Shipped: general Масови операции UI (commits 8c4c1f8 backend, 11b5793 frontend)

- `GET /api/catalog/mass-columns` — the VERIFIED whitelist (col, title, type, numeric, group, write
  path) + live tax / unit select options. Verified round-trip fields only: `actual_price`,
  `avg_delivery_price`, `code`, `barcode`, `tax`, `amount_type_id`, `is_for_sale`, `is_public`,
  `is_service`, plus the 3 localized text fields. EXCLUDED (do NOT round-trip via `save_mass` on this
  install, tested one-cell-at-a-time): `min_profit_percent`, `max_discount`, `slug`, `min/max_order`,
  `min/max_amount`, `is_for_client_order`, `is_for_load`.
- `POST /api/catalog/mass-op` — set one field across selected products (Barsy-grid semantics),
  preview/confirm, owner-only, audited, gated by `BARSY_WRITE_LIVE`. Routes each field: localized text
  → `Articles_update` full-ml-merge; everything else → native `save_mass` (flags use the `_mass`
  col_name; e.g. `is_for_sale` → `is_for_sale_mass`).
- Frontend: `MassEditForm` in the product bulk bar (Каталог + Ценообразуване), over the shared
  `ProductTable` selection — grouped field picker, type-aware value control, preview + apply.
- Note: `Articles_delete` is a **soft delete** (returns 1, sets `delete_flag=1`; the row stays in the
  DB, filtered out of the active grid). Barsy has no hard-delete for articles (unlike users).

### Looped writes converted to native `save_mass`

`catalog._bulk_price_apply` (bulk price) and `pricing.apply_supplier` (markup reprice) previously wrote
one `Articles_update` per article in a paced serial loop. Both now compute the changes then persist
them in ONE chunked `save_mass` — Barsy owns the transaction, no write-burst. The inline/background-job
split (bulkjobs) is kept for the huge-batch UX; only the write primitive changed. `catalog._bulk_rename_apply`
was deliberately **left on `Articles_update`** (see the i18n exception above). `Articles_update` also
remains for single-article edits (the catalog article form).

### Fallback: mirror the operations via per-article writes

Every field the grid mass-edits is a
normal article field, and the panel already has the verified per-article write paths:

- **The mass-edit surface:** a „Масови операции" panel view = select products (the shared
  `ProductTable` already does selection) → pick a field from the catalog above → set a value or a
  delta (e.g. price ×/÷/+/− /set, like ProductBulkPricing already does for price) → Apply = loop
  **`Articles_update {article_id, article:{<field>:<value>}}`** per selected article. Verifiable
  per article, reuses the path the catalog editor already uses.
- **Mass supplier assign:** `Articles_save_suppliers` per article (already shipped for #77/#78).
- **Mass characteristics:** inline `details` on `Articles_update`, or `ArticleDetails_save` (already
  documented; the panel already pushes characteristics).
- **Mass flags / tax / units / reorder limits:** `Articles_update` per article with those fields.

This gives the owner the same „Масови операции" capability, on the panel's proven writes, without
depending on Barsy's grid payload. Calling Barsy's native `save_mass` instead (one call for N
articles, so Barsy owns the transaction) is now feasible — the wire body is captured above — but it
means teaching the connector the adminx `?bid=1` wrapped/cookie shape rather than its Basic-auth
`barsy.call`. The per-article `Articles_update` loop stays the recommended default.

Open question for the owner to prioritise: which mass operations matter for launch (price and the
is_for_* flags are the obvious ones) vs. later (characteristics, slug, intrastat, …).

## Probe hygiene

Test articles 30750 („ТЕСТ-МАСОВИ-1") + 30751 („ТЕСТ-МАСОВИ-2") created via `Articles_create`,
used for the earlier `save_mass` API attempts, then `Articles_delete{article_id}` — both confirmed
gone on re-read. Test article 30752 („ТЕСТ-МАСОВИ-CAP") created for the live Chrome capture, price
edited to 12.34 via the UI, then `Articles_delete{article_id:30752}` → resp `1`; re-read returns
„Няма артикул с id:30752" (HTTP 501) = confirmed gone. No test residue left in Barsy.
