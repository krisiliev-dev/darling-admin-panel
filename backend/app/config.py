# -*- coding: utf-8 -*-
"""Backend settings. Reads ~/.hermes/.env (shared with the rest of the Darling systems)
plus a local .env, so Shopify/Barsy creds already present there are visible here too."""
import os
import secrets
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_HERMES_ENV = Path(os.path.expanduser("~/.hermes/.env"))
_LOCAL_ENV = Path(__file__).resolve().parent.parent / ".env"
# admin.db lives beside the backend — the panel OWNS this file (auth + audit).
# It is deliberately separate from agent-pipeline/pipeline.db, which the live
# enrichment fleet writes; we never add tables there.
_ADMIN_DB = Path(__file__).resolve().parent.parent / "admin.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # later files win on key collisions; keep hermes first so a local .env can override
        env_file=(str(_HERMES_ENV), str(_LOCAL_ENV)),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Дарлинг · административен панел"
    admin_db_url: str = f"sqlite+aiosqlite:///{_ADMIN_DB.as_posix()}"

    # Auth. In dev a random secret is fine (invalidates sessions on restart); set
    # ADMIN_JWT_SECRET in ~/.hermes/.env for a stable one once this leaves the laptop.
    admin_jwt_secret: str = ""
    jwt_lifetime_seconds: int = 60 * 60 * 12  # 12h working day
    cookie_secure: bool = True  # send session cookie over HTTPS only; set COOKIE_SECURE=0 for plain-HTTP dev

    # CORS — the Vite dev server. Tighten for production origin later.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Barsy⇄Shopify webhook receivers (HANDOFF_connector.md) -----------------------
    # Shopify signs every webhook with HMAC-SHA256(raw_body, app_secret) in X-Shopify-Hmac-Sha256.
    # Set this to the store's webhook signing secret. Empty => receivers refuse (not configured).
    shopify_webhook_secret: str = ""
    # Barsy doesn't sign payloads, so we gate its receiver on a shared token embedded in the
    # registered URL (?token=…). Empty => Barsy receiver refuses. Generate a long random value.
    barsy_webhook_token: str = ""
    # MASTER SAFETY GATE. False (default) => receivers verify + log a dry-run PLAN but write NOTHING
    # to Barsy/Shopify. Flip to True (WEBHOOKS_LIVE=1) ONLY at store launch, after the fiscal POS is
    # configured and stock quantities are loaded. Until then real orders would mis-fiscalise.
    webhooks_live: bool = False
    # Order ingestion mode: 'clientorder' logs each order for staff review first (safer for stock
    # accuracy); 'sale' finalises + fiscalises immediately. Default review-first per the handoff.
    order_ingest_mode: str = "clientorder"

    # nekorekten.com risky-customer lookup (COD refuser defense). Server-side API key (keep it in
    # env, never the browser); the whitelisted IP on nekorekten's side must be THIS server's egress
    # IP. Empty => the panel falls back to a manual deep-link lookup only, no API calls.
    nekorekten_api_key: str = ""
    nekorekten_api_base: str = "https://api.nekorekten.com/api/v1"
    # deep-link template the UI opens for a manual lookup ({phone} substituted)
    nekorekten_search_url: str = "https://nekorekten.com/bg?search={phone}"

    # Reverse-proxy trust. The rate limiter keys on client IP; X-Forwarded-For is client-
    # spoofable, so we IGNORE it unless the panel actually sits behind a trusted proxy.
    # Set TRUST_PROXY=1 ONLY when a proxy you control terminates the connection and appends
    # the real client IP as the last XFF hop (nginx proxy_add_x_forwarded_for).
    # IMPORTANT: uvicorn MUST run with --no-proxy-headers (see .claude/launch.json). Otherwise
    # uvicorn's own ProxyHeadersMiddleware rewrites request.client from XFF for localhost peers
    # BEFORE this app sees it, silently defeating this gate. With --no-proxy-headers, uvicorn
    # never touches request.client and THIS flag is the single control over proxy trust.
    trust_proxy: bool = False

    @property
    def jwt_secret(self) -> str:
        return self.admin_jwt_secret or _DEV_SECRET

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# one ephemeral dev secret per process when none is configured
_DEV_SECRET = secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
