# Admin panel — E2E coverage map

Every page + significant component mapped to the Playwright e2e spec that exercises it, grouped by the
**e-commerce operator user-flow** it belongs to. This is the checklist behind "each and every page, tab
and button does what it's supposed to." Two test layers exist and are both listed:

- **e2e** — Playwright specs in `frontend/e2e/*.spec.ts`. Render the real built SPA in Chromium, mock
  `/api/**` via `page.route` (see `e2e/helpers.ts` `mockApi`), drive it like a user. This is the
  regression net for "the page renders + the controls work."
- **unit** — vitest component tests in `frontend/src/**/*.test.tsx` (logic/rendering in isolation).

Status: ✅ covered · 🟡 partial (needs more cases) · ⬜ gap (to write).

Runner note: run against a **warm** dev server (`npm run dev` once, then
`npx playwright test`) — a cold per-run server times out `page.goto` under parallel workers.

---

## Flow 1 — Auth / session
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Login.tsx` (login + first-run signup) | `/` when logged-out | `auth.spec.ts` | ⬜ login success → shell; bad creds → error; first-run signup path; rate-limit lockout copy |
| `Shell.tsx` (nav, role-gating, theme) | shell wrapper | covered indirectly by every spec; dedicated `nav.spec.ts` | 🟡 add: every nav item routes; manager vs owner sees/【hides】 Склад + Настройки |
| `CommandPalette.tsx` (Cmd+K) | global | `nav.spec.ts` | ⬜ open with Cmd+K → jump to a page |

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
| `Intake.tsx` (Нов продукт: barcode+images editor) | `/intake` | `intake.spec.ts` | ⬜ barcode entry → enrich gate → save-to-Barsy button state; mandatory-images guard |

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
| `Orders.tsx` (Онлайн поръчки: 3-tab card, pack, label, create-order) | `/orders` | `orders.spec.ts` | ✅ 🟡 add: create-order flow; per-order label action (test-mode) |
| `CourierBadge.tsx` | orders | `orders.spec.ts` / `CourierBadge.test.tsx` | ✅ (unit) |
| `Carts.tsx` (Кошници: live/abandoned checkouts) | `/carts` | `carts.spec.ts` | ✅ |

## Flow 6 — COD reconciliation (наложен платеж)
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Cod.tsx` (Разчети: settle, aging, payout import) | `/cod` | `cod.spec.ts` | ✅ 🟡 add: payout-file import parse |
| `CustomerRisk.tsx` (nekorekten refuser flag) | COD/orders | `cod.spec.ts` | ⬜ assert the risk badge renders on a flagged customer |

## Flow 7 — Inventory / receiving / purchasing
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Deliveries.tsx` (Доставки: receiving hub, upload/scan/finalise) | `/purchasing` | `deliveries.spec.ts` | ✅ (+ `Deliveries.test.tsx`) |
| `Zayavki.tsx` (Заявки: per-supplier carts, email/export) | `/zayavki` | `zayavki.spec.ts` | ✅ (+ `Zayavki.test.tsx`) |
| `Sklad.tsx` (Склад: stock, "за поръчка", owner-gated) | `/sklad` | `access-control.spec.ts` (gate ✅) · `sklad.spec.ts` (content ⬜) | 🟡 gate done (owner renders / manager redirected); still ⬜ tab content + "за поръчка" expand |
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

## Flow 10 — Assistant (Канцелария)
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Kancelaria.tsx` / `Assistant.tsx` (in-panel BG Q&A + gated actions) | `/kancelaria`, `/assistant` | `assistant.spec.ts` | ⬜ ask a question → answer renders; a gated action shows the human-confirm step |
| `FloatingAssistant.tsx` | global | `assistant.spec.ts` | ⬜ open/close the floating widget |

## Flow 11 — Admin / settings
| Page/Component | Route | e2e spec | Status |
|---|---|---|---|
| `Settings.tsx` (Настройки: couriers, users, owner-gated) | `/settings` | `access-control.spec.ts` (gate ✅) · `settings.spec.ts` (content ⬜) | 🟡 gate done (owner renders / manager redirected); still ⬜ courier tab, create-user validation, Дневник tab |
| `Logs.tsx` (audit logs) | via Settings/Logs | `logs.spec.ts` | ✅ |

## Cross-cutting
| Component | e2e spec | Status |
|---|---|---|
| `ErrorBoundary.tsx` | `nav.spec.ts` | ⬜ a thrown render error shows the fallback, not a white screen |
| `Paper.tsx`, `PageHeader.tsx` | structural — asserted implicitly on every page | ✅ |

---

## Gaps to write (priority order)
1. **`auth.spec.ts`** — login success/failure/first-run (the front door; nothing else runs without it).
2. **`settings.spec.ts`** + **`sklad.spec.ts`** — the two **owner-gated** pages; must prove a manager is
   redirected (real access-control regression risk) + the courier-offices tab.
3. **`nav.spec.ts`** — every nav item routes; role-based nav visibility; Cmd+K palette; ErrorBoundary.
4. **`intake.spec.ts`** — Нов продукт enrich-gate + mandatory-images guard + save-to-Barsy button states.
5. **`assistant.spec.ts`** — Q&A answer render + gated-action confirm step.
6. Fill the 🟡 partials (promo/rename forms, create-order, payout import, customer-risk badge).

## Findings surfaced while writing specs
- **Nav/route role mismatch** (owner decision pending): `Shell.tsx` lists `/sklad` + `/settings` under
  `roles: ['owner','manager']` so a **manager sees the nav links**, but `App.tsx` gates the routes on
  `me.is_superuser` (owner only) and **redirects the manager to `/`**. Either the nav should hide them
  (`roles: ['owner']`) or the routes should admit managers. This is the open owner-vs-manager decision
  from the security audit — `access-control.spec.ts` locks the current *route* behaviour either way.

## Live-flow tests (separate from mock e2e)
The mock-e2e above never touches Barsy/Shopify. The **live** order journey (Shopify checkout → webhook →
panel → Barsy account, and the storno reversal on cancel/refund) is verified by observation across the
three agents, then **frozen into an e2e test using the captured webhook payload as the fixture** — see
`E2E_TEST_PLAN.md` for that cross-system checklist.
