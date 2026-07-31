# Дарлинг 7 ООД — Admin Panel: Handoff

**Written 2026-08-01.** For continuing this work in a fresh context. Everything needed to pick up
without the prior conversation. Read the whole thing once before touching anything.

---

## 1. The business and the systems

**Дарлинг 7 ООД** is a Bulgarian home-goods retailer: a physical shop plus an online store that has
**not launched yet**. Everything below is pre-launch, which is why so much is gated off.

| System | Role | Who owns it |
|---|---|---|
| **Barsy** (Lukanet SaaS ERP) | **System of record.** Stock, prices, sales, fiscal documents, users. | Hermes agent |
| **Microinvest** | Legacy ERP, source of the original catalogue + prices | — |
| **Shopify** | Current storefront — **being replaced** (see §7) | storefront agent |
| **WooCommerce** | The storefront it is moving to | storefront agent |
| **Admin panel** | The daily-driver shop-floor tool. **This is my lane.** | me |
| **agent-pipeline / pipeline-v3** | Product enrichment (images → categorisation → attributes → Bulgarian prose) | Hermes |

Money facts that shape most decisions:
- **Cash on delivery (наложен платеж) is 55–60% of Bulgarian orders.** Any COD bug is a systemic one.
- Currency is **EUR**. Prices are **VAT-inclusive** and originate as Microinvest „Продажна цена"
  **verbatim** — the AI never touches a price. (This killed a feature; see §6.)
- Bulgarian fiscal law (Наредба Н-18) applies: fiscal receipt (бон) at packing, accountant-approved.

---

## 2. My role and the lane boundaries

I own the **Admin panel** end to end: FastAPI + async SQLAlchemy backend, React 19 / Vite / TypeScript
frontend, all-Bulgarian UI, "paper/desk" aesthetic. Deployed at **https://panel.darlinghome.bg**.

Two peer agents work in parallel, in separate sessions:

- **Hermes** — Barsy connector, the enrichment pipeline, the brand registry data. Owns
  `agent-pipeline/` and `pipeline-v3/`.
- **Storefront agent** ("Website platform recommendation") — Shopify → Woo migration, courier
  storefront integration, the mail server, the Woo box.

**Lane discipline that matters:** when something belongs to another agent, ask rather than edit. I broke
this once by copying files into their directory on a false alarm (§6). Cross-session messages are the
communication channel; the owner reads summaries, not the raw exchanges.

---

## 3. Panel architecture

```
admin-panel/
  backend/           FastAPI, SQLAlchemy async, Alembic, pytest (993 tests)
    app/
      routers/       one module per surface
      core.py        the ONLY bridge to shared business modules
      core_drift.py  detects production running code the deploy didn't carry (§6)
      models.py      admin.db tables (panel-owned state)
      config.py      settings + all the write gates
    migrations/      alembic; `alembic upgrade head` runs on deploy
  frontend/          React 19 + Vite, Playwright e2e (91 tests)
  darling_core/      facade over the shared modules
    _vendor/         BUNDLED COPY of the shared modules — what production imports
  ops/               server cron scripts (version-controlled, deployed by hand)
  docs/              this file, privacy draft, compliance
```

### The two databases
- **`admin.db`** — panel-owned state (users, audit log, drafts, deliveries, issues, brand overrides).
- **`pipeline.db`** — the product catalogue, lives in `agent-pipeline/`, **read-only** to the panel.

### Write gates (all default OFF pre-launch)
`barsy_write_live`, `cod_barsy_post_live`, `delivery_barsy_live`, `store_ops_barsy_live`,
`courier_live`, `nepostop_report_live`, `webhooks_live`.
Gate OFF = the endpoint **dry-runs** and returns the exact body it *would* have sent. This is how the
Barsy write shapes were validated without touching live data.

### Access model (owner decision 2026-07-30)
Two **independent** axes:
- **Business authority**: owner / manager / staff / packer. Barsy roles map onto these at login.
- **System access**: an `is_sysadmin` **capability**, granted by the owner only.

System surfaces (Настройки internals, integrations, audit, logs, GDPR tools, user admin) require
owner **or** sysadmin. A Barsy Мениджър keeps every business function but no longer reaches those.

> **Why a capability and not a fourth role:** `barsy_login` overwrites `role` **and** `is_superuser`
> from the Barsy role map on **every** sign-in. A sysadmin stored in `role` would be silently wiped at
> the user's next login. This is the kind of constraint that must be rediscovered if forgotten.

---

## 4. Deployment

`git push origin main` → GitHub Actions → tests → deploy over SSH → **post-deploy smoke check**.

- Host: Contabo VPS `169.58.69.210`, Docker + Caddy (auto-HTTPS), repo
  `Lora-git-hub/darling-admin-panel` (private).
- SSH is hardened: key-only, `AllowUsers darling`, fail2ban, `restrict` + **forced command** on the CI
  key, and the **host key is pinned** in the `SERVER_SSH_HOST_KEY` secret (not `ssh-keyscan`, which was
  trust-on-first-use on every run).
- `workflow_dispatch` is enabled — the deploy job accepts it, so a manual run genuinely deploys.
- **Post-deploy smoke check fails the deploy** unless the live app reports `db: ok`, `db_products > 0`,
  and `core_drift: []`.

> ⚠ If the box is rebuilt, deploys fail with a host-key mismatch. That is **correct**. Do not "fix" it
> by restoring `ssh-keyscan`; update the secret from the box's own key.

### Verification discipline — read this before claiming anything works
1. **Capture exit codes explicitly.** `cmd | grep` returns grep's status. Use `EXIT=$?` and check it.
2. **A subset test run is not a gate.** Run the full suite (~12–15 min) before pushing.
3. **CI's conclusion, not the watch command's exit.** `gh run view --json conclusion`.
4. **A `_vendor` resync invalidates the previous suite run.** Re-run it.
5. **Check the far end.** After deploy, curl `/api/health`.

---

## 5. What is built and live

Everything below is committed, CI-green and deployed.

**Orders & money** — Онлайн поръчки queue; one-click **Етикет** (finalises the Barsy sale → prints the
бон → books the courier label → marks fulfilled); COD reconciliation against courier payouts; storno for
uncollected; connector anomalies surfaced in **Проблеми**.

**Catalogue** — Продукти with filters/bulk ops; full Barsy article field coverage (simplified default +
"⚙ Разширени настройки"); Ценообразуване (repricing); data-quality audit; publish-readiness audit (#81).

**Склад (store ops)** — Зареждане, **Изписване**, **Ревизия** (count → save → close), Прехвърляне,
Сторно, Движения. All built on payloads **captured from Barsy's own admin UI**, not guessed.

**Intake** — Доставки (receiving) → Приемане (enrichment) → batch to Barsy as one storeload; Нов продукт.

**Admin** — sysadmin capability; Barsy user re-role and **block**; audit log; ОРЗД/GDPR tools; Фактури
exception queue.

**New this session** — VIES ДДС validation module; nepostop COD-refuser module; „Поща" tab (link-out);
**Марки** brand-registry review queue; core-drift detector; ops scripts (backup + peer check).

---

## 6. Hard-won lessons — the most valuable section

Every one of these cost real time. They repeat.

### The silent-failure catalogue
Seven distinct bugs in three days, all of which **looked like success**:

| # | Bug | Why it was invisible |
|---|---|---|
| 1 | `rclone … 2>/dev/null \|\| true` in the backup | R2 upload failures produced no stderr and exit 0 |
| 2 | `$(curl -w '%{http_code}' \|\| echo 000)` | curl **both** prints `000` and exits non-zero → `"000000"`, matching neither test → reported OK for a host that doesn't exist. **Hit independently by two agents the same afternoon.** |
| 3 | `str.replace()` with an absent pattern | No-ops silently; the script reported "all edits applied" |
| 4 | mypy errors under a green pytest run | `Found 3 errors` scrolled past `979 passed` |
| 5 | Speedy COD fee always 0.00 | Quote path and booking path disagreed; loss was per-order and silent |
| 6 | **The deploy gap** (see below) | CI green + deploy green + health green + change absent |
| 7 | A monitor that returned "clean" when the **check itself** errored | Failed open, exactly as the things it watched did |

**The rule that catches all of them: test the RESULT, never the code.** Not one was found by reading.

**Corollaries, each learned the hard way:**
- *A failure test that doesn't fail is itself a false green.* My first attempt to break rclone pointed at
  a nonexistent bucket — it exited 0. Confirming the test actually broke the thing is part of the test.
- *"I verified it" means nothing unless the verification could have failed.* I "confirmed" a test ran
  without credentials by unsetting env vars — the module read `~/.hermes/.env` regardless.
- *Assert the negative too.* A detector only ever observed returning empty is indistinguishable from one
  that does nothing.
- *A test that only passes on a developer machine is not coverage.* Either make it
  environment-independent, or move the assertion to the environment that can make it (that is why the
  post-deploy smoke check exists).

### The deploy gap (#98) — the worst of them
`darling_core` preferred the sibling `../agent-pipeline/*.py` for imports. **Nothing deploys that
directory** — no cron, no systemd, its local `.git` has no remote. It was updated by two people
remembering to `scp`. So a fix landed in `_vendor`, passed CI (CI has no sibling, so it tests `_vendor`),
deployed green, reported healthy — **and did not run**. A stale `courier.py` under-charged the Speedy COD
surcharge on every order for an unknown period.

**Fixed:** imports come from `_vendor`; the data root is separate (`PIPELINE_DB`, set by `darling_core`
itself so it holds on dev machines too, not only in the container). Plus the drift detector and the
post-deploy check.

**The first attempt at that fix broke production** — `db.py` resolves `pipeline.db` next to *itself* and
never consults `CORE_ROOT`, so moving the import moved the data lookup. My tests asserted the variable I
had changed, not the behaviour that mattered. **The test now reads an actual product.**

### Barsy-specific rules
- **The docs are neither complete nor authoritative.** Verify against the live system. Several "this
  method doesn't exist" verdicts were wrong — the method existed under a different name
  (`roles_edit`, not `Roles_create`).
- **Never blind-sweep `_save` methods.** `Accounts_save {}` **created a real account**.
- **Learn payloads by capturing Barsy's admin-UI XHR**, then build against that. Everything in Склад
  was built this way, and it is why those modules work.
- **Read-modify-write is mandatory** on `users_edit`: the entire form travels and a partial body
  **blanks** every omitted field.
- **`users_delete` is a HARD delete** — exposed nowhere. Offboarding sets `delete_flag`.
- **Do not normalise Barsy's inconsistencies into shared helpers.** `id: 0` for a new Storeouts doc but
  `id: null` for a new Storemoves one; row `article_id` is an int while header ids are strings. Send
  each verbatim.

### Design principles that keep recurring
- **Fail closed on money, fail open on advice.** A guard that silently proceeds is worse than none; but
  a *risk lookup* that blocks orders when the lookup fails is worse than no lookup.
- **Three states, never a boolean**, for anything external: verified / not-verified / **unchecked**.
  "The service was down" and "the answer is no" are different facts.
- **Show blast radius before the click**, not in a report after.
- **Record which source and when, not a merged score** — a customer disputing a flag deserves an
  answerable reason.
- **Watch staleness, not errors.** A stopped poller looks exactly like a quiet day.
- **A wrong value nobody can see is worse than a loud one.**

---

## 7. The WooCommerce migration (owner decided 2026-07-28)

The storefront moves Shopify → WooCommerce. Driver: Shopify gates checkout customisation behind Plus
(~$2.3k/mo), so the courier office picker and VAT-invoice capture sit on the **cart** page — and
express-checkout buttons **bypass them entirely**, so a customer can pay without choosing an office.
No live orders / customers / SEO yet, so it will never be cheaper.

**Panel work is Phase 5, ~3–5 days, NOT started.** Key facts:
- The panel makes **zero direct Shopify calls**. `fetch_orders()` already returns a **normalised dict**,
  so a `woo_orders` adapter emitting the same shape keeps Онлайн поръчки, Home, insights and COD working
  unchanged. **This is the migration's single best asset.**
- Real exposure: the webhook receiver (~100 Shopify-shaped lines) and **Кошници**, which mirrors Shopify
  checkout webhooks Woo has no native equivalent for (their own plugin will emit cart events instead).
- ⚠ **Woo webhooks have no retries and silently self-disable after 5 consecutive failures** — but those
  are *defaults we control*, since we self-host. The **polling reconciler is the source of truth**
  regardless, because a fire-and-forget delivery whose *sender* may not be running cannot be the ledger.

---

## 8. Task backlog

### Unblocked — pick these up first
| # | Task |
|---|---|
| **#91** | Wire **VIES** ДДС validation into the Фактури queue. Module built + tested; not surfaced. Three states, never blocking. |
| **#92** | Wire **nepostop** into Онлайн поръчки as an advisory flag. Module built + tested; key installed. Advisory only, fail open, record source + timestamp. |

### Blocked on someone else
| # | Task | Waiting on |
|---|---|---|
| #87 | Фактури queue completion | Hermes's `list_invoice_requests` deploy |
| #93 | Кредитно известие / анулиране / проформа | Hermes's Barsy calls |
| #94 | **Приложение 38** monthly НАП XML | Owner: УИН, **currency + XSD version**, `paym` mapping |
| #96 | Courier adapters (BOX NOW, Sameday, Pigeon, Европът) | Owner: signed contracts → credentials + API docs |
| #97 | „Поща" stage 2 — order-context inbox | Storefront agent's headers-only endpoint |
| #89 | Woo Phase 5 | Their Phase 0 (second VPS, payment KYC) |

### Notable design decisions already made (don't relitigate)
- **#93**: enforce credit-note vs cancellation by **flow**, not labels — a credit note is reachable only
  from an order with an issued фактура, so `parent_inv_id` comes from context and is never typed.
- **#97 stage 2**: **headers only** cross the boundary. The mail box keeps IMAP credentials and exposes
  sender/subject/date; the panel matches sender → orders **locally** where the order data already is. A
  panel compromise then exposes subjects, not correspondence. **No snippet** — a snippet is a body
  fragment, and the most sensitive one.
- **#99**: registry ⊕ overrides; the pipeline owns the machine data, the panel owns human corrections.

---

## 9. Owner action items (all still open)

1. **`min_inv_num`** — set the фактура series to continue Microinvest's, **before any invoice is issued**.
2. **`COURIER_LIVE`** — switch on when ready for real waybills.
3. **Launch cleanup** — revision 2, depot 2 "ТЕСТ", write-offs 3/4/5, invoices 0000000001–2, empty
   account 36, test orders/clientorders, 38 test products, articles 5161/5167 (+1 from a capture).
4. **Rotate API keys** — including a **Shopify token that reached git history** (private repo,
   short-lived, self-renewing; real revocation = rotating the app client secret).
5. **Lawyer**: `docs/PRIVACY_COD_CHECKS.md` — the COD-risk lookup discloses a customer's phone number
   and nothing in the current policy covers it. Four questions listed there.
6. **Accountant**: the анулиране deadline (if a cut-off exists, the panel can *disable* the wrong option
   rather than explain it); Приложение 38 currency + current XSD version; the `paym` mapping.
7. **Barsy**: least-privilege API role for the connector user `claude` (currently Мениджър; calls 24
   methods, touches no `Users_*`/`Roles_*`). Harden **subtractively**, in the Barsy UI.
8. **Two Barsy UI captures** if store-ops needs extending further.
9. **External uptime monitor** — the cross-box peer checks cover one box dying, not a Contabo
   account/DC failure taking both. Needs a monitor outside both.

---

## 10. Working agreement with the owner

- Bulgarian UI, English code/comments. Comments explain **why**, especially where a naive change would
  be wrong.
- The owner asks for verification and **is often right to**. Several corrections this session came from
  him challenging a claim — including one that revealed production wasn't running the fix at all.
- Prefer stating a blocker plainly over half-building. Scaling work down is the owner's call.
- Don't start structural changes at the end of a long session. Both production incidents happened that
  way.
