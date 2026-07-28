# Admin panel — E2E coverage map

Every page + significant component mapped to the Playwright e2e spec that exercises it, grouped by the
**e-commerce operator user-flow** it belongs to. This is the checklist behind "each and every page, tab
and button does what it's supposed to." Two test layers exist and are both listed:

- **e2e** — Playwright specs in `frontend/e2e/*.spec.ts`. Render the real built SPA in Chromium, mock
  `/api/**` via `page.route` (see `e2e/helpers.ts` `mockApi`), drive it like a user. This is the
  regression net for "the page renders + the controls work."
- **unit** — vitest component tests in `frontend/src/**/*.test.tsx` (logic/rendering in isolation).

Status: ✅ covered · 🟡 partial (needs more cases) · ⬜ gap (to write).

⚠ **What ✅ means here.** The mock-e2e layer drives the UI against `page.route` stubs. A ✅ on a page that
performs **money/stock writes** (Онлайн поръчки, Разчети) means *the view renders and its controls are
reachable* — it does **not** mean the write action was driven. Those are marked 🟡 with the missing
action spelled out, because a spec that only renders will not catch a broken settle/label/storno.
Last honesty pass: **2026-07-28**.

Runner note: run against a **warm** dev server (`npm run dev` once, then
`npx playwright test`) — a cold per-run server times out `page.goto` under parallel workers.

---

## Flow 1 — Auth / session
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Login.tsx` (login + first-run signup) | `/` when logged-out | `auth.spec.ts` | ✅ login success → shell; bad creds → error; first-run setup; show/hide password |
| `Shell.tsx` (nav, role-gating, theme) | shell wrapper | `nav.spec.ts` + `access-control.spec.ts` | ✅ all nav items render + hrefs; client-side nav; role-gated routes (access-control) |
| `CommandPalette.tsx` (Cmd+K) | global | `nav.spec.ts` | ✅ palette opens → filter → jump to a page |

## Flow 2 — Home / cockpit
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Home.tsx` (KPIs, attention, pack queue, period toggle) | `/` | `home.spec.ts` | ✅ (+ `Home.test.tsx` unit) |
| `Stat.tsx` | — | `home.spec.ts` (rendered) | ✅ |
| `StaleStockAlert.tsx` | Home/Analytics | `home.spec.ts` (attention strip) / `analytics.spec.ts` | 🟡 assert the alert box itself |

## Flow 3 — Catalogue & product management
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Products.tsx` + `ProductExplorer.tsx` (Каталог: filters, selection, bulk bar) | `/products` | `products.spec.ts` | ✅ (+ `ProductExplorer.test.tsx` unit) |
| `Catalog.tsx` | (legacy → Products) | `products.spec.ts` | ✅ |
| `Intake.tsx` (Нов продукт: barcode+images editor) | `/intake` | `intake.spec.ts` | ✅ empty list + add; editor renders; **enrich gated on barcode**; **Save blocked w/o images** |

## Flow 4 — Pricing (store + channel)
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `ProductBulkPricing.tsx` — BulkBar (× ÷ + − =) | `/products` | `pricing.spec.ts` / `products.spec.ts` | 🟡 assert an apply → confirm → write |
| `ProductBulkPricing.tsx` — **OnlinePriceForm** (🌐 Онлайн цена) | `/products` | `online-pricing.spec.ts` | ✅ preview → apply → confirm → write; cancel aborts |
| `ProductBulkPricing.tsx` — PromoForm (🏷 промоция) | `/products` | `pricing.spec.ts` | ⬜ set a period promo |
| `ProductBulkPricing.tsx` — BulkRenameForm (🖊 по рецепта) | `/products` | `pricing.spec.ts` | ⬜ rename-by-recipe preview |
| `Pricing.tsx` (Ценообразуване: cost+markup repricing) | `/products` tab | `pricing.spec.ts` | ✅ |
| `Confirm.tsx` (in-app confirm/alert modal) | global | `online-pricing.spec.ts` | ✅ confirm + cancel branches exercised |

## Flow 5 — Order fulfilment
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Orders.tsx` (Онлайн поръчки: 3-tab card, pack, label, create-order) | `/orders` | `orders.spec.ts` | 🟡 **RENDER-ONLY** — asserts the 3 tabs, that the Нова пратка form opens, and the до-офис search folds in. The spec mocks an **empty** order list, so no order row, no **Етикет** (= finalise + бон + decrement + label + ship) and no **Откажи** is ever clicked. ⬜ add: an order row → Етикет → assert the POST to `/api/orders/{id}/label`; Откажи → Confirm → POST |
| `CourierBadge.tsx` | orders | `orders.spec.ts` / `CourierBadge.test.tsx` | ✅ (unit) |
| `Carts.tsx` (Кошници: live/abandoned checkouts) | `/carts` | `carts.spec.ts` | ✅ |

## Flow 6 — COD reconciliation (наложен платеж)
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Cod.tsx` (Разчети: settle, aging, payout import, storno) | `/cod` | `cod.spec.ts` | 🟡 **RENDER-ONLY** — one test: the KPI tiles, two order rows, a discrepancy hint, and that the Синхронизирай-проследяване button is *visible* (never clicked). ⬜ add the three writes: **settle** (`POST /api/cod/settle` — must assert it posts NO Barsy payment), **storno** (`POST /api/cod/{id}/storno`, confirm branch), **payout import/auto-pull** preview → prefilled settle lines |
| `CustomerRisk.tsx` (nekorekten refuser flag) | COD/orders | `cod.spec.ts` | ⬜ assert the risk badge renders on a flagged customer |

## Flow 7 — Inventory / receiving / purchasing
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Deliveries.tsx` (Доставки: receiving hub, upload/scan/finalise) | `/purchasing` | `deliveries.spec.ts` | ✅ (+ `Deliveries.test.tsx`) |
| `Zayavki.tsx` (Заявки: per-supplier carts, email/export) | `/zayavki` | `zayavki.spec.ts` | ✅ (+ `Zayavki.test.tsx`) |
| `Sklad.tsx` (Склад: stock ops, owner-gated) | `/sklad` | `access-control.spec.ts` (gate ✅) · `sklad.spec.ts` (content ✅) | ✅ gate (owner renders / manager redirected) **+ content**: the six operation tabs with Зареждане active, the Изписване чл.79 VAT warning + reason picker, the Сторно storeload-id field, the read-only Движения ledger empty state, and a **load submit that dry-runs and shows the Barsy body without writing**. 🟡 remaining: the "за поръчка" expand |
| `TableControls.tsx` (shared filter/sort/paginate) | list pages | `TableControls.test.tsx` | ✅ (unit) |

## Flow 8 — Analytics
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Dashboard.tsx` (Анализи: Преглед/Архив/Склад, dead-stock filters) | `/analytics` | `analytics.spec.ts` | ✅ |

## Flow 9 — Brands & taxonomy
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Brands.tsx` (Брандове) | `/brands` | `brands.spec.ts` | ✅ |
| `BrandPicker.tsx` | intake/brands | `brands.spec.ts` / `intake.spec.ts` | 🟡 |

## Flow 10 — Back-office (Канцелария) + Assistant
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Kancelaria.tsx` (label-print desk: search → queue → print) | `/kancelaria` | `kancelaria.spec.ts` | ✅ search → queue → copies → clear (print action not driven) |
| `Assistant.tsx` (in-panel BG Q&A + gated actions) | `/assistant` | `assistant.spec.ts` | ✅ empty state + example prompts; ask → the posted question is asserted, the grounded answer + model tag render, and a suggested action is clicked → in-app navigation. 🟡 only the safe `goto` suggestion kind is exercised — a *write* suggestion's confirm step is still ⬜ |
| `FloatingAssistant.tsx` | global | `assistant.spec.ts` | ⬜ open/close the floating widget (the spec deliberately scopes to `<main>` so the floating instance can't double-match — it is never driven) |

## Flow 11 — Admin / settings
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Settings.tsx` (Настройки: couriers, users, owner-gated) | `/settings` | `access-control.spec.ts` (gate ✅) · `settings.spec.ts` (content ✅) | ✅ gate **+ content**: team-from-Barsy, courier status, assistant model chain and the ОРЗД panel all render; the publish-readiness check fires and shows the summary + a blocked variant; the GDPR phone lookup returns and renders. 🟡 remaining: create-user validation, Дневник tab |
| `Logs.tsx` (audit logs) | via Settings/Logs | `logs.spec.ts` | ✅ |

## Flow 12 — Sync health (Проблеми)
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Issues.tsx` (Проблеми: connector/webhook anomalies) | `/issues` | `issues.spec.ts` | ✅ open anomaly + detail + resolve (Confirm→POST); clean empty state |

## Cross-cutting
| Component | e2e spec | Status |
|---|---|---|
| `ErrorBoundary.tsx` | `nav.spec.ts` | ⬜ a thrown render error shows the fallback, not a white screen |
| `Paper.tsx`, `PageHeader.tsx` | structural — asserted implicitly on every page | ✅ |

---

## Gaps to write (priority order)
1. ~~`auth.spec.ts`~~ ✅ done — login success/failure/first-run/password-toggle.
2. ~~access-control (gate)~~ ✅ done (`access-control.spec.ts`) — owner renders / manager redirected on
   Склад + Настройки. Still ⬜: their **content** (`settings.spec.ts` courier tab + create-user;
   `sklad.spec.ts` tab content).
3. ~~`nav.spec.ts`~~ ✅ done — nav items + hrefs, client-side nav, command palette. Still ⬜: **ErrorBoundary**
   fallback (a thrown render error shows the fallback, not a white screen) — hard to trigger via API mock;
   needs a forced-throw hook.
4. ~~`intake.spec.ts`~~ ✅ done — enrich-gate + mandatory-images guard. ~~`kancelaria.spec.ts`~~ ✅ done.
5. ~~`assistant.spec.ts`~~ ✅ done — empty state, ask→answer+model tag, suggestion→navigate. Still ⬜: a
   *write* suggestion's confirm step, and `FloatingAssistant` open/close.
6. ~~`settings.spec.ts` / `sklad.spec.ts` content~~ ✅ done — Настройки panels + publish-audit + GDPR
   lookup; Склад's six tabs, чл.79 warning, Сторно ref field, Движения empty state, dry-run load submit.
   Still ⬜: create-user validation, Дневник tab, "за поръчка" expand.
7. **⚠ TOP PRIORITY — the money-write actions.** `orders.spec.ts` and `cod.spec.ts` are RENDER-ONLY:
   neither drives Етикет (finalise + бон + decrement + label + ship), Откажи, settle, or storno. These
   are the highest-consequence buttons in the panel and nothing regression-tests them. Write:
   an order row → Етикет → asserted POST; Откажи → Confirm → POST; settle → POST with **no Barsy
   payment**; storno → confirm-gated POST; payout import/auto-pull → preview → prefilled settle lines.
8. Fill the remaining 🟡 partials (promo/rename forms, create-order, customer-risk badge).

## Follow-up fix flagged (not a test gap)
- ✅ **`window.confirm` sweep DONE** — `Confirm.tsx` now exposes `useConfirm()` + `usePrompt()`, and every
  native `window.confirm/alert/prompt` was converted to the in-app modal (CustomerRisk, Brands, Deliveries,
  Intake, Orders, Pricing, Settings, Zayavki). No native browser dialogs remain (only the
  Confirm.tsx fallback defaults). All theme-aware, keyboard-navigable, CDP-safe.
  (`Purchasing.tsx` was also swept at the time; the file has since been **deleted** — it was an unrouted
  second receiving UI against a divergent `/api/purchasing` contract. `/purchasing` renders `Deliveries`.)

## Findings surfaced while writing specs
- **Nav/route role mismatch** — ✅ RESOLVED. `Shell.tsx` listed `/sklad` + `/settings` under
  `roles: ['owner','manager']` (manager sees the links) but `App.tsx` gated the routes on
  `me.is_superuser` (owner only), bouncing managers. Owner decided to **admit managers to the routes**;
  App.tsx now gates on `role ∈ {owner, manager}` (matches the nav), staff/packer redirected. Backend
  still enforces finer per-action limits (couriers owner-only, managers create only non-privileged roles).
  `access-control.spec.ts` now asserts all three roles (owner ✅, manager ✅, staff redirected).

## Live-flow tests (separate from mock e2e)
The mock-e2e above never touches Barsy/Shopify. The **live** order journey (Shopify checkout → webhook →
panel → Barsy account, and the storno reversal on cancel/refund) is verified by observation across the
three agents, then **frozen into an e2e test using the captured webhook payload as the fixture** — see
`E2E_TEST_PLAN.md` for that cross-system checklist.
