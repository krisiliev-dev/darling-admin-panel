# End-to-end test plan — full-system walkthrough (2026-07-27)

Gates flipped for live testing (test data, cleaned/re-imported at launch). **Hard exclusions: NO POS fiscal printing (бон), NO real courier labels** (test/validation calls only). Fiscal is safe by construction — the POS has no fiscal device, so no бон can fire. Each verified flow → captured as a Playwright spec.

Data note: `db_products` (9,241) = old Microinvest analytics history, NOT the Barsy catalogue. Barsy test catalogue = `product_cache` (~30, synced from Barsy).

## A. Panel → Barsy (direct writes — gates live now)
- [ ] A1. **Cache sync** (Ценообразуване → ↻ Синхронизирай) → confirm ~30 Barsy products appear
- [ ] A2. **Bulk store price** (×/÷/+/−/set) → Barsy `actual_price` moves
- [ ] A3. **🌐 Онлайн цена** (exact/%/fixed/clear) → Barsy pricelist rule (delivery-type scoped) created+active; verify `current_price@address` diverges, store price unchanged
- [ ] A4. **Промоция** (timed % / € / exact) → Barsy Pricelists rule over a date window
- [ ] A5. **Bulk rename по рецепта** → Barsy `article_name`
- [ ] A6. **Нов продукт** (barcode → enrich → images → save) → Barsy article create (cat_id, brand, GTIN, weight, oversell)
- [ ] A7. **Доставки/Приемане** (delivery intake batch) → Barsy storeload (price_mode=1, EUR)
- [ ] A8. **Склад — Зареждане / Изписване / Прехвърляне / Ревизия / Сторно** → Barsy stock moves (confirm=true).
  Ревизия is TWO steps by design: the panel writes the counts (`Revisions_save`, `mode:"changes"` — only
  counted rows travel), and **«Приложи промените» is applied in Barsy** — that request was never captured,
  so the panel returns 501 rather than guessing it. Прехвърляне still needs a 2nd depot.
- [ ] A9. **Продукт detail edit** (name/desc/barcode) → Barsy article_update
- [ ] A10. **Наличности** low-stock + stale-stock alerts
- [ ] A11. **Заявки** supplier PO (email/export; draft storeload)
- [ ] A12. **In-store label** (58mm price/barcode) — print preview only

## B. Shopify → Barsy (webhooks — WEBHOOKS_LIVE + Shopify topics registered)
- [ ] B1. **Test PREPAID order** (financial_status=paid) → Accounts_place `flag_close_account:1` → verify: account CLOSED, `store_amount` −qty, fiscal:0 payment booked, **NO receipt_num/fm_num** (⚠ first live run of flag_close_account:1)
- [ ] B2. **Test COD order** (pending) → books as a **CLIENTORDER only**: verify `store_amount` is
      **UNCHANGED** and no account exists yet. The decrement + бон happen later, on the panel's Етикет
      action (`Accounts_createfromclientorder`, `flag_close_account:1`, `print_receipt:1`, payment under
      "Наложен платеж" id 6 against the anonymous client) → then verify stock −qty and the account CLOSED
      and PAID. ⚠ If stock drops at checkout, `ORDER_INGEST_MODE` is on `sale` by mistake.
- [ ] B3. **Company invoice** (cart invoice box) → Barsy фактура (InvoiceMakeData) + auto-email
- [ ] B4. **Fulfillment** (tracking) → tracking written to the Barsy account
- [ ] B5. **Checkouts** (create/update) → **Кошници** shows active + abandoned
- [ ] B6. **Customer create** → Barsy client (Clients_createsmart)
- [ ] ~~B7. Cancellation / Refund → Accounts_storno~~ — **HELD** (fiscal + irreversible, needs fiscal POS + explicit go)

## C. Barsy → Shopify (connector push)
- [ ] C1. **Product push** → Shopify price + `darling.backorder` metafield
- [ ] C2. **Stock change** → Shopify inventory reflects Barsy
- [ ] C3. **Online price** → Shopify variant = `current_price@address`

## D. COD lifecycle (panel-driven, Разчети)
> **Model (2026-07-28): the sale is booked PAID IN FULL at packing.** Етикет finalises the clientorder
> into a closed sale paid under "Наложен платеж" (`paymethod_id 6`, `print_receipt:1`) against the
> ANONYMOUS client — that is where the бон prints and stock decrements. Reconciliation therefore
> **confirms**; it does not pay. See COD_RECONCILE_DESIGN.md.
- [ ] D1. **Settle a matched COD** (Разчети → разчети) → a `CodSettlement` row + status `cod_paid`,
      **and Barsy is UNTOUCHED**. ⚠ Verify this explicitly: check the Barsy account still shows
      `total_paid == total_sum` and **no second payment**. A payment appearing here = the double-pay
      regression (the old paymethod-5 post) is back.
- [ ] D2. **Uncollected / RTO** (Разчети → Сторнирай, `confirm=true`) → `cod_refused_to_barsy` stornos
      the sale (`storno_reason="return"`) → stock restored, order → `returned`. Gated by
      `COD_BARSY_POST_LIVE`; run it dry first. (⚠ storno is fiscal + irreversible — see B7 hold.)
- [ ] D3. COD payout-file import (`/api/cod/import-payout`) + courier auto-pull
      (`/api/cod/reconcile-auto`) → preview buckets → prefilled settle lines
- [ ] D4. **`GET /api/cod/barsy-booked`** cross-check → Barsy-booked method-6 sales with no courier
      remittance surface as `uncollected` (= the D2 worklist)

## E. Courier (NO real labels)
- [ ] E1. **Office lookup** (public endpoint + city filter, София/Свищов)
- [ ] E2. Courier readiness + quote (Speedy/Econt) — no label creation

## F. Read-only / supporting
- [ ] F1. **Начало** cockpit · **Анализи** (dead-stock/ABC/velocity/trends)
- [ ] F2. **Одит логове** (audit) reflect every write above
- [ ] F3. **Асистент** Q&A · Cmd+K palette · role gates
- [ ] F4. GDPR webhook handlers (data_request / redact)
