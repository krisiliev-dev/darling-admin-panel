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
- [ ] Panel product editor: optional **„Онлайн цена"** field → `extra_data.online_price` (empty = use physical price). Merge-preserve other `extra_data` (weight/seo).
- [ ] Backend: thread the field through the editor model + `_build_barsy_article` / catalog-update / `_save_to_barsy` (merge into extra_data, don't clobber).
- [ ] Connector (`barsy_shopify_connector.py`): variant price = `extra_data.online_price or actual_price` on create + master-data update push. **Coordinate the one-line change with the Hermes session** (shared file).
- [ ] Show both prices in the product table (physical / online) so divergence is visible.
- [ ] Tests: overlay set → online price pushed; overlay empty → actual_price pushed (fallback); extra_data merge preserves weight/seo.
- [ ] Deferred (later session): the *policy* — bulk-set online prices by delta/margin/category; optional native per-POS migration (§B).
