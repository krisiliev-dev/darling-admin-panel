# Дарлинг Admin-Panel — Lead Reviewer Report

_Scope: `admin-panel/backend` (FastAPI, wrapping `darling_core`) + `admin-panel/frontend` (React 19 / TS 6 / Vite 8). Barsy is the ERP system-of-record; Shopify is the storefront. Status: pre-launch. Reviewed 2026-07-26._

---

## 1. Executive summary

**Health verdict: SOLID but pre-launch-risky.** The architecture is coherent and unusually disciplined for its age — a clean two-database design (`pipeline.db` read-only + `admin.db` read/write), a genuine staging-before-Barsy-write pattern, real auth/RBAC (`users.py`, `barsy_auth.py`), request-level audit of every mutation (`audit.py`), and a large test suite (481 backend test functions across 41 files, plus frontend vitest + Playwright e2e in CI via `.github/workflows/ci-cd.yml`). This is not a prototype.

The risk is **not** code quality — it is two structural gaps that bite specifically at launch:

1. **The single most financially consequential path — the Shopify→Barsy money connector (`Accounts_place`, `Clientorders_create`, `Clients_createsmart`) — has zero contract coverage.** Every webhook test mocks it away (`tests/test_webhooks.py:35-39`).
2. **Barsy write-safety gating is inconsistent.** Intake/delivery/webhooks dry-run behind master flags; catalog/pricing/purchasing writes go live the moment creds exist. An owner "testing" Ценообразуване pre-launch rewrites the real catalog.

### Top 5 things to do (in order)

1. **Write the connector money-path contract test** (`tests/test_connector_contract.py`) exercising the REAL `barsy_shopify_connector` functions — assert exact `Accounts_place`/`Clientorders_create` bodies for COD, prepaid, refund→storno, cancellation, customer upsert. _(H, tests)_
2. **Unify Barsy write-gating** behind one `barsy.write()` boundary honouring a single policy flag, or explicitly document per-op that catalog/pricing are live-from-day-one. _(M, correctness/safety)_
3. **Fix the panel's create builder** (`intake._build_barsy_article`) to emit brand (`Марка` detail) and `cat_id`/`master_cat_id` — today panel-created articles land brand-less and uncategorised. _(H, correctness)_
4. **Add backend static analysis + a coverage gate to CI** — no ruff, no mypy, no `pytest-cov` today; CI never runs the frontend linter either. _(tooling)_
5. **Extract a `services/` layer** so routers stop importing each other's private underscore helpers. _(M, architecture)_

---

## 2. Architecture assessment

### What's good (keep it)

- **Two-database spine + Barsy-as-source-of-truth.** `db.py` owns `admin.db` only; `darling_core.db` reads `pipeline.db` read-only; Barsy is authoritative for articles/stock/price/sales. `admin.db` tables (`IntakeDraft`, `DeliveryBatch/Line`, `ProductCache`, `Zayavka`, `PricingRule`, `CodSettlement`, `Brand`) are explicitly **safe staging between human approval and the Barsy write**. This is the right mental model and it is applied consistently.
- **The `core.py` / `darling_core` facade.** Every route reaches Barsy/Shopify/couriers/analytics through `core.py`, never by importing `agent-pipeline` directly; `darling_core/__init__.py` prefers the real sibling and falls back to the bundled `_vendor` snapshot so the panel imports standalone/in CI. Clean seam, correct dependency direction.
- **Cross-cutting concerns are centralized.** Two global middlewares in `main.py` (auth rate-limit + mutation audit of every `POST/PUT/PATCH/DELETE` under `/api/`), a uniform `upstream.py` 502 translator, `require_roles()` factory, fail-closed role mapping (`barsy_auth.map_role` → `packer`). The register router is deliberately unmounted (staff minted by owner only). Good security posture.
- **Master safety gates exist** (`DELIVERY_BARSY_LIVE`, `WEBHOOKS_LIVE` in `config.py`) and default to dry-run returning the exact payload — an excellent pre-launch testing affordance.

### What to change

- **No service layer → routers are libraries for each other.** `catalog.py:156,474` imports `zayavki._supplier_catalog`; `zayavki.py:30-31` imports `inventory_health._products` and `intake_delivery._norm/iter_jsonl`; `deliveries.py:30` imports `intake_delivery as D` and calls `D._article_id`; `intake.py:441` imports `deliveries.autofinalise_if_ready`; `webhooks.py:126` imports `catalog.refresh_cache_for_code`; `catalog.py:567` imports `intake as I`. Several are **function-local imports** — the classic circular-import dodge — confirming the coupling is load-bearing. A leading-underscore "do not import me" name has become a cross-module API. Introduce `app/services/` (supplier_catalog, product_seed, delivery_parse, product_cache, delivery_finalise); routers become thin request→service→response adapters.
- **Duplicated Barsy-article builder that has already diverged.** `intake._build_barsy_article` (`intake.py:640-664`) and the CLI `barsy_push.build()/_details()` (`_vendor/barsy_push.py:76-154`) both build an `Articles_create` body and disagree on fields. Collapse to one shared builder in the `darling_core.barsy` layer.
- **Ad-hoc write-gating instead of one boundary** (see §1.2 and the findings table).
- **No real migration tool.** `db._backfill_columns()` only `ADD`s columns — no rename/type-change/backfill. Fine additively; will bite on the first non-additive change. Adopt Alembic before that day.

---

## 3. Prioritized findings

| Sev | Area | File | Issue | Fix | Effort |
|-----|------|------|-------|-----|--------|
| **High** | Tests / money path | `tests/test_webhooks.py:35-39` | All six connector translation fns are monkeypatched with a recording stub; tests assert only HMAC/token gating, topic→fn routing, and `do_write==webhooks_live`. The real `Accounts_place`/`Clientorders_create`/`Clients_createsmart` logic in `_vendor/barsy_shopify_connector.py` — the sole sale/payment/customer write path — is never exercised. A wrong mapping (total vs COD, line price, qty, client id, refund/storno sign) passes the whole suite. | Add `tests/test_connector_contract.py` importing the REAL functions; stub `barsy_api.call`/`_catalog`/`_seen_order`/`_paymethod_id`; assert exact bodies for paid+COD, prepaid, partial refund→storno, cancellation, customer upsert; assert refund/storno **sign** explicitly. | M |
| **High** | Correctness / duplication | `routers/intake.py:640-664` vs `_vendor/barsy_push.py:76-154` | Panel create path maps only name/unit/description/barcode/price + attribute details; it **drops `brand`** (populated at `models.py:58`, set `intake.py:458/539`) and sends **no `cat_id`/`master_cat_id`**. CLI builder writes brand as `Марка` detail and resolves category via `ensure_path`. Panel-created articles land brand-less and uncategorised, which propagates empty vendor/category to Shopify. | In `_build_barsy_article`: append `{detail_name:"Марка", detail_value:d.brand}` when set; resolve category via `barsy_taxonomy.ensure_path` from the draft's Shopify leaf and set `cat_id`/`master_cat_id`. Long-term: one shared builder in `darling_core.barsy`. | M |
| **Medium** | Safety architecture | `pricing.py:212,316`; `catalog.py:372,597,699`; `purchasing.py:148,162` vs `config.py:49,59` | Two master gates dry-run intake/delivery/webhooks, but pricing apply/apply-supplier, catalog bulk-price/bulk-rename/promo/update_article, and purchasing create/close-order write live Barsy with **no gate** — only role+confirm (and `update_article`/`create_order`/`close_order` have **no confirm at all**). Pre-launch, testing Ценообразуване or bulk-rename mutates the real catalog while the delivery screen next door dry-runs. | Add `barsy.write(method, body)` in the core layer: allowlist mutating methods, honour one `BARSY_WRITE_LIVE`-style policy, return a dry-run marker when off. Route every mutating call through it. Keep `barsy.call` for reads (don't blanket-gate — would break `Articles_get`/`Store_amounts`). | M |
| **Medium** | Coupling / architecture | `catalog.py:156,474,567`; `zayavki.py:30-31`; `intake_delivery.py:37`; `deliveries.py:30`; `intake.py:441`; `webhooks.py:126` | Routers import each other's private helpers; no service layer. Import-order fragility (function-local imports to dodge cycles), near-impossible unit isolation, private names as cross-module API. Works today; pure maintainability debt. | Create `app/services/` and move shared logic under public names (supplier_catalog, product_seed, delivery_parse, product_cache, delivery_finalise). Incremental, one helper at a time. | M |

_(All four are the CONFIRMED findings, verified against source; the raw set had these at critical/high/high/high — downgraded on confirmation because `webhooks_live`/`delivery_barsy_live` default off and the live paths are owner-only + audited.)_

---

## 4. Barsy write-back surface

Derived from the backend map's `barsy_ops` and router items. "Reflects to Barsy" = the operation issues a real Barsy API write (subject to the gate column).

| Operation | Router / entry | Barsy method | Reflects to Barsy? | Gate | Notes / gap |
|-----------|----------------|--------------|--------------------|------|-------------|
| Create article (manual intake push) | `intake.push_to_barsy` | `Articles_create` | **Yes** | owner + confirm | **Omits brand + cat_id.** Price = staff `actual_price` only. |
| Create/update article on draft save (delivery-sourced Нов продукт) | `intake._save_to_barsy` | `Articles_create`/`Articles_update` | **Yes** | `DELIVERY_BARSY_LIVE` → dry-run | Same brand/category omission. |
| Update editable fields (name/desc/barcode) | `catalog.update_article` | `Articles_update` | **Yes** | **none** (staff role only, no confirm) | Price deliberately untouched here. |
| Upload product image | `intake._upload_pending_images` / `barsy_push.push_images` | `Files_upload` (multipart) | **Yes** | tied to create path | — |
| Bulk price change | `catalog.bulk_price` | `Articles_update` (loop) | **Yes** | **none** (role+confirm) | Re-reads live baseline for relative ops. |
| Bulk rename | `catalog.bulk_rename` | `Articles_update` (loop) | **Yes** | **none** | Operates on `ProductCache` — can act on stale rows. |
| Promo / price lists | `catalog.promo` | `Pricelists_*` | **Yes** | **none** | — |
| Apply pricing rule | `pricing.apply` / `apply-supplier` | `Articles_update` `actual_price` | **Yes** | **none** (owner + confirm) | Cost-plus-markup from `admin.db` `PricingRule`. |
| Create/close purchase order | `purchasing.create_order` / `close_order` | `Storeloads_create` / `Storeloads_close` | **Yes** | **none** (staff role, no confirm) | Not covered by `DELIVERY_BARSY_LIVE`. |
| Finalise delivery (stateful) | `deliveries.autofinalise_if_ready` | `Storeloads_create` | **Yes** | `DELIVERY_BARSY_LIVE` → dry-run | Safer path (staged per-line). |
| Commit delivery (stateless) | `intake_delivery.commit` | `Articles_create` + `Storeloads_create` | **Yes** | `DELIVERY_BARSY_LIVE` → dry-run | Can't dedupe a partial retry → orphan articles on mid-batch failure. |
| Create staff | `admin.create_staff` | `Users_create` | **Yes** | owner-only + privilege guard | Only Barsy-user write. |
| Sale / payment / refund / customer (order ingest) | `webhooks` → `connector` | `Clientorders_create`, `Accounts_place` (sale/storno/refund), `Clients_createsmart`, `Articles_update` (gid backref) | **Yes** | `WEBHOOKS_LIVE` → dry-run | The money path. **Zero contract tests** (§3). |
| Zayavki (purchase requests) | `zayavki.*` | — | **No — admin.db only** | — | Stock recorded later at Доставки. |
| COD settlement | `cod.settle` | — | **No — admin.db + pipeline fulfillment flip** | — | **Never posts payment to Barsy** — cash ledger can diverge from Barsy. |
| Order queue + courier labels | `orders.*` | — | **No** | — | Fiscalization is the webhook connector's job. |
| Brands gazetteer/store | `brands.*` | — | **No — admin.db only** | — | — |
| Product list | `products.*` | — | **No — reads pipeline.db** | — | Read-only over `dim_product`. |

### Open questions to confirm against the Barsy API docs

1. **Brands: is there a first-class brand field / brands table, or only free-text `Марка` details?** The barcode/EAN memory notes Darling's own `380013` GS1 prefixes and a brand worklist; the connector maps a Barsy detail → Shopify `vendor`. Confirm whether Barsy exposes a structured brand entity so the panel writes to it rather than a loose detail string.
2. **Native bulk article update?** `bulk_price`/`bulk_rename` currently loop `Articles_update` per SKU. If Barsy offers a batch endpoint, it removes N-call latency and partial-failure ambiguity.
3. **Supplier create/update (`Suppliers_create`/`_update`)?** Today suppliers are read-only (`Suppliers_getlist`, resolved by name→id); a brand-new supplier must be created in Barsy by hand or the storeload **silently drops `supplier_id`**. Confirm the write endpoint exists (or add a hard warning when `_resolve_supplier_id` returns `None`).
4. **Category API for panel create.** Confirm the `cat_id`/`master_cat_id` resolution path (`barsy_taxonomy.ensure_path`) the CLI uses is the intended one, so panel-created articles are categorised.
5. **COD→Barsy payment posting.** Does the connector-created account accept an `Accounts_place` payment at COD-settle time, or is Barsy's cash ledger authoritative only via order-ingest? Decide whether `cod_settlement` posts into Barsy or stays a reconciliation overlay.
6. **Article webhook registration** (`Webhooks_subscribe`, connector ~line 464) — confirm the subscription shape so `ProductCache` stays fresh for out-of-panel Barsy edits; until registered, `/catalog/sync` is mandatory before any bulk op.

---

## 5. Test-suite assessment + backlog

**Current state (good breadth, one critical hole).** 481 backend test functions across 41 files (`test_build_barsy_article.py`, `test_push_guards.py`, `test_role_gating.py`, `test_security_hardening.py`, `test_ratelimit.py`, `test_cod_*.py`, `test_migration.py`, `test_product_cache.py`, plenty of `test_cov_*` coverage-fillers). `pytest.ini` sets `asyncio_mode=auto` and an opt-in `integration` marker (`RUN_INTEGRATION=1`, `test_integration_live.py`). Frontend: vitest unit tests (11 `.test.tsx`/`.test.ts` files) + 7 Playwright e2e specs (`frontend/e2e/*.spec.ts`), both wired into CI.

**The hole:** the Barsy **money path is mocked in every test that touches it** (`test_webhooks.py`). The catalog/pricing/purchasing live-write paths are exercised for dry-run/guard behavior but there is no assertion of the exact mutating body sent to Barsy for the ungated paths. No `pytest-cov` → no visibility into what the 481 tests actually cover, and no coverage floor in CI.

### Backlog by type

**Contract tests (highest value — Barsy/Shopify body shapes):**
- `test_connector_contract.py` — REAL `shopify_order_to_barsy` (paid+COD → `Accounts_place` payment + rows; prepaid → online-payment path), `shopify_refund_to_barsy` (partial → storno sign/qty), `shopify_order_cancellation_to_barsy` (full storno referencing original `account_id`), `shopify_customer_to_barsy` (`Clients_createsmart` name/phone/email/export_id), `barsy_article_to_shopify` (gid backref). Assert `missing_skus` is surfaced, not dropped.
- `test_build_barsy_article_fields.py` — extend the existing builder test to **assert brand `Марка` detail and cat_id/master_cat_id are present** (locks in the §3 fix).
- Catalog/pricing write-body contract: assert exact `Articles_update` bodies from `bulk_price` (relative vs absolute), `bulk_rename`, `pricing.apply`, and `promo` `Pricelists_*`.
- Storeload contract: `Storeloads_create` body from `deliveries.finalise` and `intake_delivery.commit`, including `supplier_id` present/absent behavior.

**Unit tests:**
- `barsy_api.call` error semantics — a 200 with unexpected shape returned as success; `_extract_article_id`/`_article_id` parsing int|str|dict; intake marking a draft `pushed` when id can't be parsed (strand-vs-duplicate).
- `_backfill_columns` additive-only behavior (guards against expecting it to rename/backfill).
- `map_role` fail-closed to `packer` (has coverage — keep).

**Integration tests (against a Barsy sandbox / recorder, opt-in marker):**
- End-to-end create: intake draft → `Articles_create` → image upload → cache refresh.
- Delivery finalise → `Storeloads_create` with real supplier resolution.
- Webhook receiver → connector → recorder, with `WEBHOOKS_LIVE` on in a sandbox.

**E2E / user-journey (Playwright — extend existing specs):**
- **COD reconciliation journey**: order → label → deliver → settle → payout import → divergence check (the cash-ledger split-brain in §4).
- **Delivery intake journey**: parse xlsx → scan-confirm → OCR → finalise (dry-run assertion of the storeload payload surfaced in UI).
- **Pricing journey**: define rule → preview → apply → confirm the live-write warning is shown (locks in the gating decision).
- Add a `pricing.spec.ts` and `cod.spec.ts` (neither exists in `frontend/e2e/`).

---

## 6. Tooling & health plan + stack assessment

### Stack assessment

Modern and well-chosen. **Backend:** FastAPI + async SQLAlchemy + fastapi-users + pydantic-settings — idiomatic, current, appropriate for an API-over-ERP wrapper. **Frontend:** React 19, TypeScript 6, Vite 8, Tailwind 4, Framer Motion, Chart.js, React Router 7 — a current, coherent, well-maintained set. No red-flag dependencies. The "paper design system" and EUR-native decisions are documented in memory. The stack is not the problem; the process guardrails are thin.

### Gaps and the plan

| Area | Today | Recommendation |
|------|-------|----------------|
| **Backend lint** | None (no ruff/flake8; no `pyproject.toml`/`ruff.toml`) | Add **ruff** (lint + format) with a `pyproject.toml`; run in `backend-ci`. Fast, zero-config, replaces black+isort+flake8. |
| **Backend types** | None (no mypy/pyright) | Add **mypy** (or pyright) starting `--ignore-missing-imports` on `app/`, ratchet toward strict on the money/gating modules first (`connector`, `intake`, `pricing`, `catalog`). |
| **Frontend TS strictness** | `tsconfig.app.json` has `noUnusedLocals/Parameters` + `noFallthroughCasesInSwitch` but **no `"strict": true`** | Turn on `strict` (or at least `strictNullChecks` + `noImplicitAny`). This is a real gap for a data-heavy admin app; a null price/quantity slipping through is exactly the class of bug that mis-books money. |
| **Frontend lint in CI** | `oxlint` configured (`.oxlintrc.json`) and `npm run lint` exists, but **CI never runs it** — `ci-cd.yml` runs build/vitest/playwright only | Add a `npm run lint` step to `frontend-ci`. Expand oxlint rules beyond the current two (rules-of-hooks + only-export-components). |
| **Coverage gate** | No `pytest-cov`; no vitest coverage threshold; no gate in CI | Add `pytest-cov` with a floor (start ~70%, ratchet), and a vitest `coverage.thresholds`. Fail CI below floor. Prioritize covering `connector`, `intake`, `pricing`, `catalog`. |
| **Pre-commit** | None | Add `.pre-commit-config.yaml`: ruff (lint+format), mypy (fast subset), oxlint, end-of-file/trailing-whitespace. Mirror the CI checks locally. |
| **Migrations** | `_backfill_columns` additive-only | Adopt **Alembic** before the first non-additive `admin.db` change. |
| **CI deploy safety** | `deploy` runs on every push to `main` after tests; concurrency-guarded | Consider gating deploy behind the new coverage/lint jobs too, so a green deploy implies lint+types+coverage passed. |

---

## 7. Suggested execution sequence

**Phase 0 — guardrails first (unblocks everything, ~½ day):**
1. Add `pyproject.toml` with ruff; add `pytest-cov`; add `npm run lint` + strict `tsconfig`; wire all into `ci-cd.yml`. Fix the fallout (mostly mechanical). This makes every later change safer to land.

**Phase 1 — protect the money path (do before `WEBHOOKS_LIVE` ever flips):**
2. Write `test_connector_contract.py` (§5). This is the launch blocker — it converts the mocked routing test into a real contract test.
3. Only after the contract test is green, make the §3 gating decision (Finding 3) and implement the single `barsy.write()` boundary — or explicitly document catalog/pricing as live-from-day-one.

**Phase 2 — correctness of panel-created records:**
4. Fix `intake._build_barsy_article` to emit brand + category (Finding 2); add the field-assertion test (§5). Confirm open questions 1 and 4 against the Barsy docs first.
5. Confirm open question 3 (supplier create) and add the hard-warning fallback when `_resolve_supplier_id` returns `None`.

**Phase 3 — structural cleanup (no behavior change, done incrementally):**
6. Extract `app/services/` and migrate the cross-router private-helper imports one at a time (Finding 4); collapse the two Barsy-article builders into the shared one.
7. Adopt Alembic; add the remaining e2e journeys (COD, pricing) and integration tests against a Barsy sandbox.

**Cross-cutting:** register the Barsy article webhook (open question 6) as part of the launch checklist, and resolve the COD→Barsy payment-posting decision (open question 5) before COD volume ramps.