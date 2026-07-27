# Inventory reconciliation — the finalise → ship → fulfilment/on_hand sequence

**Task #83.** Prevents a sold unit being counted **twice** in Shopify `available` once the live Barsy→Shopify
stock sync runs. Surfaced by COD#4 (see memory `inventory-reconciliation-rule`). Not urgent (state is
consistent today) but **launch-critical before the live inventory sync is enabled.**

## The invariant
Shopify computes `available = on_hand − committed`. A Shopify order's `committed` and Barsy's stock
decrement **represent the same physical unit**, so:

> **Exactly one of {Barsy→Shopify on_hand mirror, Shopify committed} may reduce `available` for a given
> sale — never both, and never neither.**

Violate it two ways:
- **Double-count** (drop by 2): mirror pushes `on_hand = Barsy(8)` while the order is still `committed=1`
  → `available = 7`. ← the risk today.
- **No reservation** (oversell): write `available` directly (clobbers `committed`) → a second customer can
  buy the reserved unit.

## Lifecycle (correct behaviour)
| Event | Barsy | Shopify | `available` |
|---|---|---|---|
| Order placed | untouched (9) | `committed +1` | 9 − 1 = **8** ✓ |
| **Finalise** (staff) | decrement 9→**8** | **no push** (see rule A) | 9 − 1 = **8** ✓ (unit still committed + in warehouse) |
| **Ship / dispatch** | (already 8) | **fulfilment** (`committed 1→0`) **+ on_hand mirror → 8** | 8 − 0 = **8** ✓ |
| Refuse (pre-finalise) | untouched (9) | cancel order (`committed 1→0`) | 9 − 0 = **9** ✓ |

## Rules to implement
**Rule A — the on_hand mirror must not push a finalise-decrement while the order is still committed.**
`barsy_quantity_change_to_shopify(code)` fires on the Barsy `articles_availability` webhook. For a code that
has **open (un-shipped) Shopify orders committing it**, the mirror must either:
- (preferred) push `on_hand = Barsy physical stock` as it does today (absolute, `inventorySetQuantities
  name:"on_hand" @idempotent`) — this is already reservation-safe because Shopify derives
  `available = on_hand − committed` itself. The ONLY additional requirement is **rule B** (fulfilment clears
  committed at ship, so the two never overlap for a *shipped* unit). i.e. today's mirror is correct **as long
  as ship-time fulfilment happens** — see rule B. Do NOT ever write `available`/`committed` directly.

**Rule B — at ship/dispatch, push a Shopify fulfilment for the order.** This is the missing piece: nothing
fires a Shopify fulfilment today (verified on #1004 — post-finalise it stayed `UNFULFILLED`, `committed=1`).
Ship must:
1. Create a Shopify fulfilment for the order's line items (`fulfillmentCreate`) — this moves the quantity
   out of `committed` and reduces Shopify `on_hand` by the fulfilled qty **once**.
2. Ensure the Barsy on_hand mirror does **not** *also* decrement for the same unit at the same moment — the
   fulfilment already dropped Shopify on_hand. Since Barsy decremented at **finalise** (earlier) and the
   mirror pushes Barsy's *absolute* physical stock, the safe ordering is: **fulfilment first, then (if a
   mirror runs) it sets on_hand to Barsy's current value** — which already reflects the finalise decrement,
   so the net is one drop, not two. Concretely: **the mirror is absolute-set, the fulfilment is committed-
   clearing; run them so the final Shopify on_hand == Barsy physical stock and committed == 0 for shipped
   lines.**

## Where the trigger lives
- **Ship/dispatch** is the natural trigger. Today the panel's **Етикет** (label creation) is the ship action
  (`POST /api/orders/{id}/label` → sets op-status `shipped`). That endpoint should, on success, call a
  connector `ship_order(order_id)` that pushes the Shopify **fulfilment** (+ tracking number from the label)
  and reconciles on_hand. Tie the fulfilment to the label so tracking + fulfilment + inventory move together.
- Alternatively a dedicated **"Изпрати/Диспечирай"** action if label ≠ ship in the final workflow.

## Ownership
- **Connector (Hermes):** `ship_order(order_id, tracking=None, do_write=False)` → `fulfillmentCreate`
  (with tracking) + the on_hand reconcile per rules A/B. Dry-run returns the plan.
- **Panel (Claude):** call `ship_order` from the label-success path (or a dedicated ship button); gate +
  audit like finalise/refuse. Surface any failure to **Проблеми** (the connector returns `{status,reason}`).
- **Storefront:** confirm the `fulfillmentCreate` + `inventorySetQuantities(name:"on_hand")` shapes for the
  live API version, and that fulfilment is created **without** a customer "shipped" email if we don't want
  one at that step (or with, if ship == notify).

## Test plan (once a fresh order flows)
1. Order → Shopify `committed=1, available=on_hand−1`.
2. Finalise → Barsy −1; Shopify unchanged; `available` still correct.
3. Ship (label) → Shopify fulfilment (`committed→0`) + on_hand == Barsy; **`available` unchanged, counted
   once** (the key assertion — it must NOT drop by 2).
4. A Barsy stock change (receiving) → mirror → Shopify on_hand tracks Barsy, reservations preserved.
