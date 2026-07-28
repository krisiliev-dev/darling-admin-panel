# COD reconciliation — how it works (BUILT) + the small remainder

**Status (2026-07-28): BUILT AND SHIPPING.** Manual payout-file import, courier auto-pull, and the
shared matcher are all live in `backend/app/routers/cod.py`. Only two data fields remain unbuilt (see
"Not yet built" at the bottom). This doc was previously a *design* for the auto-pull; it is now the
description of the running system, corrected for the anonymous paid-at-packing COD model.

## ⚠ THE RULE THAT MUST NOT BE BROKEN — a matched COD writes NOTHING to Barsy
Under the anonymous-PAID model the sale is booked **PAID IN FULL at packing**: Етикет is the single
pack action and it finalises the clientorder into a closed sale with a payment under the dedicated
**"Наложен платеж"** method (`COD_PAYMETHOD_ID = 6`, `print_receipt:1`) against the ANONYMOUS client —
that is the moment stock decrements and the fiscal бон prints
(`agent-pipeline/barsy_shopify_connector.py:finalise_order`).

So by the time a courier payout arrives, Barsy already believes the money is in.

| outcome | what reconciliation does |
|---|---|
| **matched** (courier remitted) | **CONFIRM ONLY** — a `CodSettlement` row + status → `cod_paid`. **NO Barsy write.** |
| **uncollected / RTO** | **STORNO** — `connector.cod_refused_to_barsy` (`storno_reason="return"`), reverses the false "paid" + returns the goods to stock. Gated by `COD_BARSY_POST_LIVE`. |

Posting the collected cash into Barsy on settle would **double-pay** the account
(`total_paid > total_sum`) and double-count the COD money. The old `_post_cod_payment_to_barsy`
(paymethod 5) was deleted for exactly this reason — see the comment block at `cod.py:327` and the
tombstone at `cod.py:384`. The connector's `cod_payment_to_barsy` is marked OBSOLETE. **Do not
resurrect either.** The accountant signed off on бон-at-packing (Н-18).

## The join key
Every payout line is matched to an order by **waybill/tracking number**, normalised to digits only
(`_norm_tracking`) because couriers pad and space them. The key is stored at settle time in
`CodSettlement.waybill` (indexed) and lives per-order in `fulfillment_state.tracking`.

## What is built

### 1. Manual file import — `POST /api/cod/import-payout` (`cod.py:392`)
Upload a courier payout/deduction CSV. Tolerant parser (`_parse_payout`): sniffs the delimiter,
decodes utf-8/cp1251/latin-1, finds the tracking + amount columns by header keyword and then
**by value shape** (longest-digit column for the waybill, most-decimal column for money) so decoys
like "Номер на клиент" / "Платец" don't win. Returns a PREVIEW only — writes nothing.

### 2. Courier auto-pull — `POST /api/cod/reconcile-auto {courier, date_from, date_to}` (`cod.py:473`)
Staff-gated. Calls `adapter.payout_report(date_from, date_to)` on the Speedy/Econt adapters
(`darling_core/_vendor/courier.py:389` and `:606`), normalises the rows, and feeds the same matcher.
Rejects anything but `speedy`/`econt`; 503 if the courier isn't configured, 502 on a courier error.
Wired in the UI at `frontend/src/pages/Cod.tsx:166`.
⏳ *Live-validation pending*: the shapes are coded against the documented APIs but have not run against
a real payout window (no real shipments yet). Re-check field names on the first real payout cycle.

### 3. Shared matcher — `_reconcile(rows, orders)` (`cod.py:422`)
Used by BOTH paths, so the file import and the auto-pull can never drift. Buckets:
- **matched** — waybill found on an awaiting/in-transit order; flags `discrepancy` (collected ≠ expected)
  and `out_of_range`.
- **unmatched_rows** — payout line with no order of ours (paid-not-ours / unknown).
- **delivered_not_paid** — our `awaiting` order absent from the payout (the courier owes us).
- **settle_lines** — ready-to-submit lines that the operator posts through `/api/cod/settle`, kept as
  the single write path.

### 4. The Barsy cross-check — `GET /api/cod/barsy-booked` (`cod.py:195`)
Reads every finalised method-6 sale via `connector.list_cod_accounts` — "what Barsy believes it
collected" — and marks the ones no courier has remitted as `uncollected`. That list IS the storno
worklist. Read-only; skips already-stornoed accounts.

### 5. Uncollected → storno — `POST /api/cod/{order_id}/storno` (`cod.py:344`)
The only Barsy write in the whole COD surface. Requires `confirm=true` + the staff role + audit; 409s
on an already-settled order so a collected sale can never be reversed. Flips the order to `returned`.

## Not yet built (genuinely open — do NOT mark these done)
- ⬜ **`expected_fee` per order** (courier commission estimated at label time). Without it we can flag a
  short *collection* but not a short *payout* — the `fee` a payout row carries is passed through to the
  preview but nothing compares it. This is the missing half of "short-paid → dispute candidate".
- ⬜ **`payout_status` per settlement** (matched / short / disputed) for the audit trail. Today
  `CodSettlement` stores expected/collected and the discrepancy is derived, not classified.

Both are small column additions on `CodSettlement` (`backend/app/models.py:257`). Build them after the
first real twice-weekly payout cycle, when there's real fee data to calibrate against.
