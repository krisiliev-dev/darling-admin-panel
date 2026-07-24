# COD reconciliation — auto-pull design (build post-launch)

**Status: DESIGNED, not built.** The manual payout-file import already ships (`/api/cod/import-payout`).
This documents the automatic version to build once real courier payout data exists.

## Why now (data model)
Auto-reconciliation matches each courier payout line to one of our orders by **waybill/tracking
number**. Retrofitting that key is painful, so it's already stored: `CodSettlement.waybill` (indexed),
populated at settle time from the order's tracking. `fulfillment_state` also holds `tracking` per order.

## The two courier sources
- **Speedy** — `POST /payments` returns shipment payout details for a period (amount collected, fees,
  transfer date, waybill). Speedy API: https://doc.speedy.io/
- **Econt** — per-shipment `CD_send_sum` (amount paid out) + `CD_send_time`; bank transfer next business
  day after collection. Econt COD API: https://www.econt.com/developers/39-nalozhen-platezh.html

Both are already-authenticated in the panel (courier adapters). Add a `payout_report(date_from, date_to)`
method to each adapter returning normalized rows: `{waybill, collected, fee, payout_date, status}`.

## The endpoint to build
`POST /api/cod/reconcile-auto {courier, date_from, date_to}` (owner-only):
1. Pull the courier payout rows for the window.
2. Join to our COD orders by normalized waybill.
3. Bucket, exactly like the manual importer already does:
   - **matched** — waybill found; collected vs expected (flag discrepancy), fee vs expected fee.
   - **short-paid** — deduction > expected courier commission → dispute candidate.
   - **paid-not-ours** — payout line with no matching order.
   - **delivered-not-paid** — our awaiting order missing from the payout (courier owes us).
4. Return the same preview shape as `import-payout` so the existing `Разчети` UI (PayoutImport +
   SettlePanel prefill) renders it unchanged — reuse the front end.
5. On settle, post the reconciled cash into **Barsy** (Payments_create against the order's Accounts
   record) so the fiscal system of record stays clean — but only once the webhook connector is live
   and orders exist in Barsy as accounts (see WEBHOOKS_LAUNCH.md).

## Also store (small additions when built)
- `expected_fee` per order (courier commission estimate at label time) → enables short-paid detection.
- `payout_status` per settlement (matched / short / disputed) for the audit trail.

## Effort / sequencing
Small-to-medium once payout files exist to test against. Build **early post-launch**, after the first
real twice-weekly payout cycle. Until then the manual importer covers it.
