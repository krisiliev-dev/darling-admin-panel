# -*- coding: utf-8 -*-
"""Дарлинг admin panel — FastAPI service. Wraps darling_core as a clean API and serves
the auth surface for the React front end."""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import ratelimit


def _configure_logging() -> None:
    """Send the app's INFO logs to stdout so `docker logs darling-panel` shows them. Without this the
    container only surfaces uvicorn's own lines (root logger defaults to WARNING with no stdout handler),
    so webhook processing — including the connector's 'unmatched SKU' refusals and 'processed' lines —
    was invisible server-side (we could see the Caddy 200 but not what the handler did with it). Covers
    every app logger (`app.*`) plus the connector's `darling.*`. Idempotent; noisy libs stay at WARNING."""
    root = logging.getLogger()
    if any(getattr(h, "_darling_stdout", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler._darling_stdout = True  # type: ignore[attr-defined]  # marker so we don't double-add on reload
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # keep the chatty libraries from drowning the signal
    for noisy in ("sqlalchemy.engine", "aiosqlite", "httpx", "httpcore", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
from .config import get_settings
from .core import health as core_health
from .core_drift import core_drift, log_core_drift
from .db import create_db_and_tables
from .issues import record_issue
from .routers import (
    admin,
    assistant,
    auth_extra,
    brand_registry,
    brands,
    brands_v2,
    carts,
    catalog,
    cod,
    dashboard,
    deliveries,
    gdpr,
    home,
    insights,
    intake,
    intake_delivery,
    inventory,
    inventory_health,
    invoices,
    issues,
    logs,
    online_pricing,
    orders,
    pricing,
    products,
    public_couriers,
    publish_audit,
    purchasing,
    quality,
    risk,
    store_ops,
    summary,
    webhooks,
    zayavki,
)
from .schemas import UserRead, UserUpdate
from .users import auth_backend, fastapi_users

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()  # create admin.db (users + audit) if missing
    # Say at startup whether we are running the shared core this repo shipped. A deploy that changes
    # nothing is otherwise indistinguishable from one that worked (task #98), and it is recorded in
    # Проблеми so it survives past whoever happened to read the log that day.
    for d in log_core_drift():
        await record_issue(
            source="ops", topic="deploy", order_ref="", severity="error", kind="core_drift",
            summary=f"Панелът работи със стара версия на {d['module']} — деплойът НЕ я обновява. "
                    f"Поправки в този модул минават тестовете и не влизат в сила.",
            detail=d)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,  # cookie auth needs this
    allow_methods=["*"],
    allow_headers=["*"],
)

# per-ip rate cap on the credential-verifying endpoints (covers the mounted FastAPI-Users
# /login too, which we can't hook per-attempt). Fine-grained failed-login lockout lives in
# the barsy-login endpoint.
_RATE_LIMITED_AUTH = {"/api/auth/login", "/api/auth/barsy-login", "/api/auth/setup"}


@app.middleware("http")
async def _rate_limit_auth(request, call_next):
    if request.method == "POST" and request.url.path in _RATE_LIMITED_AUTH:
        try:
            ratelimit.guard_auth_request(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=e.headers)
    return await call_next(request)


# Audit EVERY mutating request (POST/PUT/PATCH/DELETE) so nothing a user does goes unlogged. Semantic
# log_action rows add the rich detail on top. Never let auditing break the request.
_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def _audit_mutations(request, call_next):
    from . import audit
    response = await call_next(request)
    if request.method in _AUDIT_METHODS and request.url.path.startswith("/api/") \
            and not request.url.path.startswith("/api/health"):
        try:
            await audit.record_request(request, response.status_code)
        except Exception:
            pass
    return response

# --- auth: cookie login/logout, current-user, first-run setup ------------------------
app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/api/auth", tags=["auth"])
# Self-service /me only. We DROP the FastAPI-Users superuser CRUD-by-id routes (/{id} GET/PATCH/
# DELETE): the panel UI never uses them, and because Barsy MANAGERS are is_superuser=True they were
# an escalation surface — a manager could PATCH the break-glass owner's credentials and take over.
# Staff accounts are managed via auth_extra → Barsy, not this router.
_users_router = fastapi_users.get_users_router(UserRead, UserUpdate)
_users_router.routes = [r for r in _users_router.routes if "{id}" not in getattr(r, "path", "")]
app.include_router(_users_router, prefix="/api/users", tags=["users"])
# NOTE: the built-in open register router is deliberately NOT mounted — staff accounts
# are created only by the owner, via auth_extra's guarded endpoint.
app.include_router(auth_extra.router)

# --- business modules ---------------------------------------------------------------
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(catalog.router)
app.include_router(carts.router)
app.include_router(brands.router)
app.include_router(brands_v2.router)
app.include_router(brand_registry.router)
app.include_router(orders.router)
app.include_router(intake.router)
app.include_router(intake_delivery.router)
app.include_router(deliveries.router)
app.include_router(zayavki.router)
app.include_router(inventory.router)
app.include_router(inventory_health.router)
app.include_router(cod.router)
app.include_router(assistant.router)
app.include_router(purchasing.router)
app.include_router(admin.router)
app.include_router(logs.router)
app.include_router(gdpr.router)
app.include_router(insights.router)
app.include_router(invoices.router)
app.include_router(issues.router)
app.include_router(pricing.router)
app.include_router(online_pricing.router)
app.include_router(public_couriers.router)
app.include_router(publish_audit.router)
app.include_router(quality.router)
app.include_router(risk.router)
app.include_router(summary.router)
app.include_router(store_ops.router)
app.include_router(home.router)
app.include_router(webhooks.router)  # Barsy⇄Shopify receivers (HMAC/token-gated, no session auth)


@app.get("/api/health", tags=["meta"])
def health():
    """Startup probe: catalog DB reachable, couriers registered, and — since 2026-07-31 — whether the
    shared core modules match this repo. `core_drift` non-empty means the panel is executing code the
    deploy did not update, so a "successful" deploy may have changed nothing (task #98)."""
    out = core_health()
    if isinstance(out, dict):
        out["core_drift"] = core_drift()
    return out
