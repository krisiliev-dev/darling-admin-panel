# Channel pricing — physical store vs. online (per-POS decision)

**Decision (owner, 2026-07-27):** prices differ per **POS/channel** — the physical store price ≠ the online (Shopify) price. The *pricing-difference policy* (how the online price is derived — manual per product, a % delta, margin rule, etc.) is deferred to a dedicated future session. This doc records the architecture + what is buildable now.

## Two implementation paths

### A) Barsy-native per-POS pricelist — BLOCKED (do not build yet)
`Pricelists_save_barsys_poses` scopes a price rule to a specific POS/каса; the online store would be a distinct Barsy POS and the rule sets its price. Correct long-term, but:
- `Pricelists_save`/`Pricelists_set` return **HTTP 501** on this install → no clean API create-path (open in `barsy_support_ticket.md` §Pricelists + §B).
- Requires the online store to exist as a **separate Barsy POS** (unconfirmed — `barsy_support_ticket.md` §B).
→ Gated on the Barsy contact's answers. Migrate to this later if native per-POS is wanted for reporting/attribution.

### B) `extra_data.online_price` overlay — UNBLOCKED (the interim build) ✅
The online price lives on the Barsy article's `extra_data.online_price` (a string, EUR, VAT-incl — same field family as `weight_kg`/`seo_*`, already written by the panel and readable via `Articles_get extra_properties:{extra_data:1}`).

- **Physical price** = Barsy `actual_price` ("Продажна цена") — unchanged, sold at the physical POS.
- **Online price** = `extra_data.online_price` when set, else falls back to `actual_price`.
- **Connector push:** `barsy_article_to_shopify` sends `extra_data.online_price or actual_price` as the Shopify variant price (was: `actual_price` only).
- **Online orders** already book back into Barsy at the line price the customer paid (`original_current_price`) — no change needed.
- **Human-set, never AI-touched** (respects the price policy; the override is an owner/staff decision).

This delivers per-channel pricing for the real setup (1 physical + 1 online) **without** the blocked Pricelists API or a second Barsy POS. It is forward-compatible: if native per-POS lands, the overlay can seed or be replaced by the Barsy rule.

## Build checklist (path B)
- [x] Panel product editor: optional **„Онлайн цена"** field → `extra_data.online_price` (empty = use physical price). Merge-preserve other `extra_data` (weight/seo). *(1f037bd)*
- [x] Backend: threaded through `ApproveRequest`/`DraftOut`/create/update + `_build_barsy_article` (merges into extra_data, omitted when blank). Migration `a1b2c3d4e5f6` (`server_default=''`). *(1f037bd)*
- [x] Connector (`barsy_shopify_connector.py`, Hermes) — helper `_shopify_price(art)` = `extra_data.online_price` (via `Articles_get extra_properties` — extra_data isn't in list payloads) else `actual_price`, applied in **4** spots, verified live (online 9.99 pushed, clean fallback):
  1. `barsy_new_product_to_shopify` minimal-create
  2. `barsy_article_to_shopify` master-data update
  3. **`sync._barsy_price`** — the ENRICHED create path (where db-enriched products, the common case, get their price)
  4. **`reconcile()` drift check** — compare against `_shopify_price` so online-priced products don't false-flag as drifted + re-push every nightly run
- [x] Tests: overlay set → pushed; blank → actual_price fallback; extra_data merge preserves weight/seo. *(1f037bd)*
- [ ] Nice-to-have: show both prices (physical / online) in the product table so divergence is visible.
- [ ] Deferred (later session, owner): the *policy* — bulk-set online prices by delta/margin/category; optional native per-POS migration (§B).

**Perf note (Hermes):** +1 `Articles_get` per push (extra_data absent from list payloads) — fine for per-article webhook events; if reconcile-at-scale gets heavy, bulk-pull extra_data via `getlist extra_properties`.

**STATUS: overlay path COMPLETE + verified live end-to-end (2026-07-27).** Physical price = `actual_price`; online price = `extra_data.online_price` when set. Zero divergence until an online price is entered.
