# Channel pricing — physical store vs. online (Barsy NATIVE per-till)

**Decision (owner, 2026-07-27):** prices differ per **till/POS (каса)** using **Barsy's native per-till pricing** — NOT a custom panel field. The physical store is one till; the **online store price is assigned to the virtual "claude" till** (the POS the API/connector operates from). The *pricing-difference policy* (how the online price is derived) is deferred to a dedicated future session.

## ❌ Reverted: the `extra_data.online_price` overlay
An earlier attempt stored the online price on `extra_data.online_price` and had the connector push `online_price or actual_price`. **This was wrong** — it reinvented pricing Barsy already does natively. Reverted end-to-end:
- Panel: `IntakeDraft.online_price` col removed (migration `b2c3d4e5f6a7` drops it, forward-only after `a1b2c3d4e5f6`), editor field removed, `_build_barsy_article` no longer writes it, tests removed.
- Connector (Hermes): `_shopify_price` overlay reverted back to `actual_price`.

## ✅ The native approach (to design + build)
Barsy supports **per-till prices**. There is already a **virtual till assigned to the `claude` API user** — every API call the connector makes operates in that till's context. So:
- **Physical store** = its own till → sells at the article's base price.
- **Online store** = the **claude/virtual till** → the online price is assigned to *that* till in Barsy.
- The connector, operating from the claude till, reads the **till-scoped price** and pushes it to Shopify. If Barsy returns the till's effective price in the normal read (Articles_get / catalog in the caller's till context), the connector may need **no special field at all** — it already reads and pushes the article price; that price simply *is* the online price when read from the claude till.

## Open questions (before building)
1. **How is a per-till price set in Barsy?** — via the UI (a price list bound to the till), or an API method? Earlier we saw `Pricelists_save`/`_set` = HTTP 501; confirm whether per-till prices use a different method or are UI-only. (Coordinate with Hermes; may fold into `barsy_support_ticket.md` §B.)
2. **How does the API read a till-scoped price?** — does `Articles_get`/catalog automatically return the price for the *calling* user's till (claude), or is a till/pricelist parameter required?
3. **Confirm the claude virtual-till id** and that the connector's reads/writes are in that till's context (Hermes owns the connector + API session).
4. Does the panel need any UI here at all, or is online pricing managed entirely in Barsy against the till? (Likely minimal/no panel change.)

## Next steps
- Hermes: revert the connector `_shopify_price` overlay to `actual_price`; confirm the claude till id + how a till-scoped price reads back through the API.
- Then design the native flow from the answers above. Panel involvement TBD (probably none, or a read-only display of the online price).
