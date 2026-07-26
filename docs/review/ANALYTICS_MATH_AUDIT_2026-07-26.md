# Analytics-Correctness Report — Дарлинг Panel

## 1. Verdict

**The dashboards are broadly trustworthy at their default settings, with two issues worth fixing.** Nine of the core inventory formulas, the ABC classification, and the entire COD reconciliation summary are mathematically correct. However, one real formula bug (**GMROI is not annualised**) silently misreports on any non-default window, and one **revenue-scope mismatch** means the trend chart and the headline "приход" tile show different totals for the same word. Everything else is either correct or a labelling nuance. Owners can trust the numbers at the default 365-day window; the GMROI tile should not be trusted on longer windows until fixed.

## 2. Confirmed math errors (most severe first)

### 2.1 GMROI is not annualised — inconsistent with turnover  — **HIGH**
- **Files:** `app/inventory_metrics.py:105-106`; hint in `frontend/src/pages/Dashboard.tsx:499-500`.
- **Shows vs should:** `turnover` annualises COGS (`total_cogs*365/window_days`, line 102-103) but GMROI divides the **raw window-cumulative** gross margin by inventory with no `365/window` scaling. The two only agree when `window_days == 365`. The endpoint accepts windows up to 1095 days (`routers/inventory_health.py:195`), so GMROI is overstated by a factor of `window/365` — up to **3×**.
- **Numeric example (window=730):** rev 500,000, COGS 300,000, inv 100,000 → code returns GMROI `(500000-300000)/100000 = 2.0`; the true annual GMROI is `(200000*365/730)/100000 = 1.0`. A break-even line reads as a star performer, and the ">1 добре" hint reinforces the doubled number. (At window=90 the same bug runs the *other* way and flags a healthy line as a capital-destroyer: GMROI 0.25 vs true 1.01.)
- **Fix:** `gmroi = (gross_margin * 365.0 / window_days) / inv_value` (equivalently `(annual_rev - annual_cogs)/inv_value`), putting GMROI on the same annual basis as turnover/DSI.
- **Note:** The **seed** diagnosis GMROI (0.81) is coincidentally correct because its window is exactly calendar-year 2025 (annual factor = 1). Only the live path is wrong.

## 3. Misleading but not wrong (right number, wrong impression)

### 3.1 Trend chart total ≠ headline revenue — **MEDIUM**
- **Files:** `app/data/seed_analytics.json` (ts/rev_year) vs `kpi.rev`; rendered `Dashboard.tsx:469,516-521`.
- Monthly buckets are internally self-consistent (per-year sums reproduce `rev_year`; 57 months sum to 973,810). **But** trend revenue **973,810** ≠ headline `kpi.rev` **962,479** (diff 11,331 €, 1.2%); units 308,722 vs 307,919 (diff 803); implied margin 40.4% vs `kpi.gm` 39.7%. The KPI comes from a per-article report scope, the trend from a monthly-document scope. A user summing the chart cannot reproduce the headline "приход".
- **Fix:** reconcile the two scopes or label distinctly — headline "приход (по артикул)" vs trend "приход (по месец)".

### 3.2 Turnover uses ending, not average, inventory — **LOW**
- `inventory_metrics.py:102-103,66`. Annualisation and cost-basis are correct, but the denominator is the current on-hand snapshot, not period-average inventory. If today's stock diverges from the window average, turnover (and DSI, which inherits this) is biased. Example: true period-average inv 25k but today 10k → reports 10.0× when true is 4.0×. Only a snapshot is available upstream, so this is an accepted proxy — label the tile "turnover vs current stock", not true period-average.

### 3.3 `dead_pct` sits next to a SKU count — **LOW**
- `inventory_metrics.py:125`; `Dashboard.tsx:301,398,502`. The base is inventory **value** (correct, most useful), but it renders as `${dead_pct}% · ${dead_n} арт.`, easily misread as "% of articles". Example: one pricey dead item = 40% of capital among 500 SKUs shows "40% · 1 арт." (true: 40% of value, 0.2% of SKUs). Fix: label it "% от стойността / от капитала".

### 3.4 Portfolio units and revenue sit on different populations — **LOW**
- `inventory_metrics.py:67-68` (revenue/turnover/DSI/GMROI sum over **all** sales rows) vs line 128 (`units` KPI and top_sellers from **stock rows only**). A SKU that sold 500 units then went to zero stock contributes to revenue/turnover but 0 to the units KPI and never appears in "Най-продавани". Not a wrong formula, but the two totals have different denominators. Fix: compute the units KPI from `s_by_id` like revenue.

## 4. Edge-case bugs (rounding / snapshot / null)

### 4.1 Money stored and summed as float, not exact — **LOW (latent)**
- `models.py:260-261` (SQLite REAL), throughout `cod.py`. Euro amounts are IEEE-754 floats; values like 19.99/25.50 aren't exactly representable and `round()` is banker's-rounding on a float repr. In practice the single `round(,2)` at each accumulator's end (`cod.py:182-183`) absorbs ~1e-10 drift for realistic volumes, so displayed стотинки are correct for thousands of orders. It only surfaces at implausible N — latent/technical, not a live wrong number, but it violates the exact-arithmetic mandate. Fix: integer cents or `Decimal`/`Numeric`.

### 4.2 Row "Очаквано" vs discrepancy basis mismatch — **edge-case**
- `cod.py:158` (row `expected` = **current** `cod_amount`) vs `cod.py:161,181` (discrepancy flag/amount use `st.expected`, the **settlement-time snapshot**, `cod.py:266`). If an order's `cod_amount` is edited after settlement, the "Очаквано" column shows a different figure than the one the ↯ разлика flag was computed against. The discrepancy math itself is correct (frozen against the snapshot); only the displayed pairing can look inconsistent. Fix: display `st.expected` in the row when a settlement exists.

### 4.3 `dead_n` off-by-one in seed — **LOW (cosmetic)**
- `kpi.dead_n = 2412` vs `meta.dead_total = 2411` in `seed_analytics.json`; frontend surfaces 2412 (`Dashboard.tsx:501-502`). Reconcile to one count.

### 4.4 ABC boundary tie-break — **none/cosmetic**
- Recompute vs seed differs by exactly one ~60 € product at the A/B boundary (0.006%), from a different tie-break convention. Harmless; document assign-before-crossing vs include-crossing-item if exactness is wanted.

**All div-by-zero paths are properly guarded:** `window_days` floored to ≥1; `turnover`/`dsi`/`gmroi` return `None` when `inv_value<=0` or no sales; `days_of_cover` `None` when `velocity==0`; `dead_pct` guarded; frontend renders "—" for nulls.

## 5. Verified correct (safe to trust)

**Inventory (`inventory_metrics.py`):**
- **velocity** = units/window_days (line 82) — standard, div-by-zero safe.
- **DSI** = 365/turnover (line 104) — expands to the standard inventory/COGS×days; `turnover==0` → None.
- **dead-stock value** — on-hand with zero window sales, summed at cost (lines 86,108).
- **reorder point** = velocity×(lead+safety) (lines 84-85) — standard days-based safety-stock model; only fires when velocity>0.
- **suggested_qty** = `max(0, ceil(velocity×(lead+safety+coverage) − qty))` (lines 88-89) — correct order-up-to; no phantom orders for dead stock; consumed consistently by Zayavki/Purchasing.
- **days_of_cover** = qty/velocity (line 83) — correct, null-guarded.
- **Frontend/backend parity** — field names, units (×, дни, €/€, money), and null-suffix suppression all align (`Dashboard.tsx:292-305,391-401`, `api.ts:426-427`).

**ABC (`routers/inventory_health.py` + seed):**
- **Classification** (sort DESC by revenue, cumulative 80/95% thresholds) — recompute matches seed within one boundary product; A=top 80%/B=15%/C=5% honoured.
- **A/B/C counts + value sums** — A+B+C value 962,480 = `kpi.rev` (1 € rounding); counts sum to 11,581 = products with rev>0.
- **Seed turnover/DSI/GMROI shape** — same formulas as live, internally self-consistent (365/1.15=317).
- **Diagnosis-tile label/unit fidelity** — every tile shows the right value, unit, and label (`Dashboard.tsx:495-504`).

**COD reconciliation (`cod.py`, `Cod.tsx`):**
- **outstanding** = in_transit + awaiting (line 185) — correct definition; settled/preship/rto excluded.
- **awaiting / in_transit amount+count** — `elif` bucketing keeps count and amount in lockstep, no double-count.
- **settled** — uses actually-collected cash with an `is not None` guard so genuine 0.00 counts as 0.
- **discrepancy detection** — 0.01 threshold applied identically across settle, overview, and preview paths; `abs()` catches both short and over payments.
- **discrepancy amount+count** — sums absolute per-order gaps (€40 short + €40 over = €80, the correct risk signal), separate from bucket totals.
- **bucket partition** — mutually exclusive and exhaustive (default → preship); no money dropped or double-counted.