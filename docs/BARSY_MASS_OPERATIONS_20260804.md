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
- Auth/transport note: this is the **adminx SPA** shape (`/endpoints/json?bid=1`, wrapped in the
  page id, cookie-authed). The connector's `barsy.call(method, body)` posts to `/endpoints/json/<method>`
  with Basic auth and an UNWRAPPED body. To replicate natively the connector would need to post the
  wrapped `{"articles_index": {...}}` form to `?bid=1` (or `barsy.call("articles_index", {action_type, active_struct_id, values})`
  which wraps method=articles_index) — but see the recommendation below: we do **not** need to.

## Recommendation: mirror the operations, not the wire format

Even though the `save_mass` body is now known (above), the panel does **not** need it. Every field the grid mass-edits is a
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
