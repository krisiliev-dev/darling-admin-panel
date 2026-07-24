# Handoff note → admin-panel build agent (from the pipeline/enrichment session, 2026-07-20)

The owner asked me to relay two things to you. (Direct session-to-session messaging went offline
mid-session, so this is a file drop — the owner may also paste it to you.)

## 1. Local Barsy API documentation — use this, don't guess the API
A Postman collection of the Barsy API is saved locally at:

    C:\Users\lora_\Desktop\product-review\agent-pipeline\barsy_docs\BarsyApi.postman_collection.json

Point your Barsy integration at it. The live client wrapper is `agent-pipeline/barsy_api.py`
(`barsy_api.call(method, params)`), and the article builder is `barsy_article_builder.py`.
Gotcha already learned: `Articles_getlist` with empty filters returns the whole list; there's a
POS-related quirk noted in the owner's Barsy memory. **Barsy currently holds only ~2 articles** —
the catalog article import (task #46) hasn't loaded products yet.

## 2. What "publishable" actually means (from a Shopify publish dry-run I just ran)
The publish path `barsy_shopify_sync.build_input(code)` reads ONLY the **enriched record's
`fields`** — specifically `Снимки` (image URLs), `Цена` (price), and `meta.leaf_gid` (Shopify
taxonomy category). A product produces a complete Shopify payload **only after pipeline v2 has
reprocessed it**; old v1-schema records lack those fields entirely.

Concrete numbers today: of 9,142 enriched records only **~2,000 are v2**; **601 are truly
publishable**. ~12,000 products (7,085 v1 needing reprocess + 5,052 never enriched) still need v2.
When v2 runs, the record comes out 100% complete (image URL + leaf + price all populated).

Implications for the panel:
- Gauge launch/publish readiness off **`launch_readiness.py`** (I just fixed it to gate on the
  real record, i.e. what `build_input` reads — so "ready" == "publishable"). Or call `build_input`
  directly and check for a non-empty payload.
- **Price policy (hard rule):** price = Microinvest "Продажна цена", copied as-is, NEVER
  AI-touched. Barsy `actual_price` wins when the article exists, else the Microinvest export.
- **Perf:** `_barsy_price()` re-pulls the entire Barsy article list per product, and
  `build_input`'s fallback rebuilds the 37k-row Microinvest price map per product — both O(N²).
  Index each ONCE before any bulk sync (I did this in `publish_dryrun.py` — reuse that pattern).
- Products are created in Shopify as **DRAFT**, never auto-published.

— pipeline session
