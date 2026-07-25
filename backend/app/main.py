# -*- coding: utf-8 -*-
"""Дарлинг admin panel — FastAPI service. Wraps darling_core as a clean API and serves
the auth surface for the React front end."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import ratelimit

from .config import get_settings
from .core import health as core_health
from .db import create_db_and_tables
from .schemas import UserCreate, UserRead, UserUpdate
from .users import auth_backend, fastapi_users
from .routers import (
    admin, assistant, auth_extra, catalog, cod, dashboard, gdpr, home, insights, intake,
    intake_delivery, inventory,
    inventory_health, orders, pricing, products, purchasing, quality, risk, summary, webhooks)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()  # create admin.db (users + audit) if missing
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

# --- auth: cookie login/logout, current-user, first-run setup ------------------------
app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/api/auth", tags=["auth"])
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/users", tags=["users"])  # owner-only CRUD (superuser-guarded)
# NOTE: the built-in open register router is deliberately NOT mounted — staff accounts
# are created only by the owner, via auth_extra's guarded endpoint.
app.include_router(auth_extra.router)

# --- business modules ---------------------------------------------------------------
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(intake.router)
app.include_router(intake_delivery.router)
app.include_router(inventory.router)
app.include_router(inventory_health.router)
app.include_router(cod.router)
app.include_router(assistant.router)
app.include_router(purchasing.router)
app.include_router(admin.router)
app.include_router(gdpr.router)
app.include_router(insights.router)
app.include_router(pricing.router)
app.include_router(quality.router)
app.include_router(risk.router)
app.include_router(summary.router)
app.include_router(home.router)
app.include_router(webhooks.router)  # Barsy⇄Shopify receivers (HMAC/token-gated, no session auth)


@app.get("/api/health", tags=["meta"])
def health():
    """Startup probe: catalog DB reachable, couriers registered."""
    return core_health()
