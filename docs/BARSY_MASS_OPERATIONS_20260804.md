# Barsy „Масови операции" (article mass edit) — captured 2026-08-04

Owner asked to mirror Barsy's mass operations on the panel. This is what Barsy's article mass edit
actually is, the full field set it can change, and the recommended way to mirror it. Captured with
two throwaway test articles (30750, 30751) that were **created, probed, and deleted — verified gone,
zero residue**.

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

## The exact `save_mass` wire body is NOT yet confirmed

Three API attempts to drive `save_mass` from the connector:
- `articles_index {action_type:"save_mass", active_struct_id:"eStructList_1", rows:[{article_id, actual_price}]}` → HTTP 500.
- `… values:{}, rows:[{key_id, actual_price}]}` → returned `1` but the price did NOT change (no-op).
- `… values:{}, rows:[{id, actual_price}]}` → returned `1`, no change.

So the UI sends a specific grid payload (probably a `values`-keyed-by-row structure, or a different
row key / column encoding) that these guesses don't match. Nailing the exact body needs a live
capture of the real request from the Barsy admin UI (Chrome + the XHR hook), the way other methods
were captured. **This is only needed if we choose to replicate `save_mass` natively — see below.**

## Recommendation: mirror the operations, not the wire format

The panel does **not** need Barsy's opaque `save_mass` body. Every field the grid mass-edits is a
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
depending on Barsy's grid payload. If instead we want to call Barsy's native `save_mass` (one call
for N articles, so Barsy owns the transaction), the next step is a Chrome capture of the real UI
request — a focused task, owner's call.

Open question for the owner to prioritise: which mass operations matter for launch (price and the
is_for_* flags are the obvious ones) vs. later (characteristics, slug, intrastat, …).

## Probe hygiene

Test articles 30750 („ТЕСТ-МАСОВИ-1") + 30751 („ТЕСТ-МАСОВИ-2") created via `Articles_create`,
used for the `save_mass` attempts, then `Articles_delete{article_id}` — both confirmed gone on
re-read. No test residue left in Barsy.
