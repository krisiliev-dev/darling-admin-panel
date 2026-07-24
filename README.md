# Дарлинг — Admin Panel

The permanent, daily-driver operations tool for Дарлинг 7 ООД. **Separate** from the temporary
reviewer site (`../agent-pipeline`), which is retired once the enrichment backlog is done.

**Full design + architecture spec:** [`design.html`](design.html) (also published as an artifact).

## Stack
- **Backend:** FastAPI (Python) — wraps the shared business-core as a clean API.
- **Auth:** FastAPI-Users + roles + audit log (no Django ORM remodel; keep the existing DB).
- **Frontend:** React + TypeScript + Tailwind + Framer Motion + shadcn/ui + Chart.js (Vite build).
- **Data:** today's SQLite (`../agent-pipeline/pipeline.db`) → Postgres when multi-user concurrent.

## Layout
```
admin-panel/
  design.html         the design + architecture spec (source of truth)
  darling_core/       Phase 0: shared business-core facade (db, barsy, shopify, courier, analytics)
  backend/            Phase 1: FastAPI app (to build)
  frontend/           Phase 1: React/Vite app (to build)
```

## Running it (dev)
Two servers. Backend on :8770, frontend on :5173 (Vite proxies `/api` → backend, so cookies
are same-origin). From `admin-panel/`:
```bash
# 1) backend  (FastAPI + auth + business API)
python -m uvicorn app.main:app --app-dir backend --port 8770 --reload
# 2) frontend (Vite dev server)  — in a second terminal
npm --prefix frontend run dev
```
Open http://localhost:5173. **First run** shows a one-time "create owner" screen; after one
owner exists it becomes the normal login. Add staff/packer accounts later as the owner
(`POST /api/auth/staff`). `.claude/launch.json` has both as named launch configs.

Backend deps: `pip install "fastapi-users[sqlalchemy]" aiosqlite "uvicorn[standard]" argon2-cffi`.
Frontend deps are in `frontend/package.json` (`npm install`).

- **Auth DB is separate:** the panel owns `backend/admin.db` (users + audit). It reads the
  catalog `pipeline.db` read-only through `darling_core` and never writes it — the live
  enrichment fleet owns that file.
- **JWT secret:** dev uses an ephemeral per-process secret (sessions drop on restart). Set
  `ADMIN_JWT_SECRET` in `~/.hermes/.env` for a stable one before this leaves the laptop.

## Roadmap
- **Phase 0 — extract the business-core** *(done).* `darling_core` re-exports the live
  modules without moving them (zero disruption to the running fleets). Physical relocation happens
  when the backlog is done and processes can restart.
- **Phase 1 — scaffold** *(done).* FastAPI service wrapping `darling_core` + React/Vite app in the
  paper design system + auth (roles + audit + first-run owner setup) + task-oriented nav shell +
  tabbed insights dashboard (Каталог live, Продажби empty-until-launch).
- **Phase 2 — daily modules:** product intake (the signature animated flow), fulfilment
  (Speedy/Econt/BoxNow/Sameday labels + COD; started in `../agent-pipeline/ops_console.py`),
  supplier orders, inventory.
- **Phase 3 — automation & the assistant:** the command box + alerts + Claude-driven automations.

## Business-core surface (Phase 0)
```python
from darling_core import db, barsy, shopify, courier, analytics, health
health()   # startup probe: DB reachable? couriers configured?
```
- `db` — SQLite system-of-record (products, orders, analytics tables).
- `barsy` — Barsy ERP client (articles, stock, suppliers).
- `shopify` — Shopify Admin GraphQL client (products, orders).
- `courier` — courier-agnostic shipping layer; adapters for Speedy/Econt/BoxNow/Sameday/Pigeon/Evropat.
- `analytics` — star-schema ETL (`dim_product`, `price_history`, `fact_sales`, `fact_purchases`) + KPI queries.

## Notes for whoever builds this next (human or agent)
- The running enrichment/review fleets and the review server import the same modules from
  `../agent-pipeline` — **do not move or rename those files while processes are live.** The facade
  exists precisely so the panel can build against a stable surface without touching them.
- Prices are Microinvest "Продажна цена", never AI-touched (price policy).
- Everything user-facing is in **Bulgarian**.
- Get this off the owner's laptop before real daily use — it hibernates.
