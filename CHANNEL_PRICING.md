# Channel pricing — physical store vs. online (Barsy NATIVE, delivery-type scoped)

**Status (2026-07-27): SHIPPED (panel + backend), gated (dry-run) until launch. Mechanism proven live.**

**Decision (owner):** the online (Shopify) price differs from the physical store price, using **Barsy's native pricing** — NOT a custom panel field. The physical store keeps its base `actual_price`; the online price is a native Barsy **price rule scoped to the online delivery types** (до адрес + офис), so it applies only to online orders on the single existing till. No 2nd till / license needed.

## How it works
- **Store price** = Barsy `actual_price` (untouched, never edited by this feature).
- **Online price** = a persistent Barsy rule `"Онлайн цени (панел)"` (type 1, exact per-article), scoped via `Pricelists_save_tax_regions` to delivery types **2 (до адрес) + 4 (офис)**, activated. Per-article prices are `custom_price` rows via `Pricelists_save_articles`.
- **Read/push to Shopify** (connector, Hermes): reads `Articles_getlist current_price` with `filters:{delivery_type_id:"address"}` → the online price; falls back to `actual_price` when a product has no online rule row.
- **Proven live** (Hermes, disposable catalog): active rule scoped to tax_region 2+4 → `current_price` = rule price for `address`/`office`, = base for `no`/in-store.

## Panel feature (this build)
Owner-only, in **Ценообразуване**, mirroring the bulk store-price ops:
- **Set** via the product bulk toolbar → **🌐 Онлайн цена**: pick a method + value over the current selection (or a whole category via the filters):
  - **exact €** — set an exact online price
  - **% of store** — online = store ±X% (e.g. +10, −5)
  - **fixed ± €** — online = store ± amount
  - **clear** — remove the online price (falls back to store price online)
- **View**: the "🌐 Онлайн цени" panel lists every product with an online override (store vs online).
- Dry-run **preview** (confirm=false) shows store→online per product; **gated** behind `BARSY_WRITE_LIVE`.

## Architecture
- `backend/app/models.py::OnlinePrice` — panel-owned intended online price per article (source of truth, like `PricingRule`). Migration `c3d4e5f6a7b8`.
- `backend/app/routers/online_pricing.py` — `POST /api/online-pricing/apply` (compute → upsert local → push the FULL set into the Barsy rule; `Pricelists_save_articles` overwrites, so the full set is authoritative and omission clears) + `GET /api/online-pricing` (list). **Safety:** every push re-asserts the online-only delivery-type scope, so the rule can never bleed into physical prices.
- `frontend`: `ProductBulkPricing.tsx::OnlinePriceForm` (toolbar) + `Pricing.tsx::OnlinePriceList` (overview); `api.ts::OnlinePricePreview/OnlinePriceItem`.
- Connector read-filter helper (`current_price@address`, fallback `actual_price`): Hermes wires at launch.

## Barsy call shapes (UI-captured 2026-07-27, validated live)
- Create rule: `Pricelists_edit {action_type:"save", active_struct_id:"eStructForm_1", values:{pricelist_name, pricelist_type_id:1, …}}` (id read back via snapshot-diff of `Pricelists_index`).
- Scope to delivery types: `Pricelists_save_tax_regions {id, action_type:"save", values:{}, rows:[{tax_region_id:"1..5", checked:0|1}]}` — online = 2+4.
- (Scope to a POS instead: `Pricelists_save_barsys_poses {id, values:{pricelist_id, pos_id:[…]}, action_type:"save_barsys_poses"}` — `values` is an OBJECT.)
- Activate: `Pricelists_set_status {pricelist_id, status:1}`.
- Per-article prices: `Pricelists_save_articles {id, rows:[{article_id, custom_price, req_amount:1, price_change_group:1}]}` (overwrites the list).

## Deferred (owner's dedicated pricing session)
The pricing **policy** (which products, what deltas/margins). The mechanism + tooling are done. Also possible later: a native auto-tracking %-modifier rule (so % online prices follow store-price changes instead of being a snapshot).
