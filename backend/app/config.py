# -*- coding: utf-8 -*-
"""Backend settings. Reads ~/.hermes/.env (shared with the rest of the Darling systems)
plus a local .env, so Shopify/Barsy creds already present there are visible here too."""
import os
import secrets
from functools import lru_cache
from pathlib import Path

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
    # Shared secret for the PUBLIC courier office-lookup (/api/public/couriers/{key}/offices), which
    # the storefront's Cloudflare Worker calls to populate the checkout office picker. The caller
    # presents it as the X-Office-Token header; courier API creds stay server-side and are never
    # exposed. Empty (default) => the endpoint is DISABLED (403). Set OFFICE_LOOKUP_TOKEN to a long
    # random value and hand it to the storefront directly. Read-only, so low-risk if it ever leaks.
    office_lookup_token: str = ""
    # MASTER SAFETY GATE. False (default) => receivers verify + log a dry-run PLAN but write NOTHING
    # to Barsy/Shopify. Flip to True (WEBHOOKS_LIVE=1) ONLY at store launch, after the fiscal POS is
    # configured and stock quantities are loaded. Until then real orders would mis-fiscalise.
    webhooks_live: bool = False
    # Order ingestion mode: 'clientorder' logs each order for staff review first (safer for stock
    # accuracy); 'sale' finalises + fiscalises immediately. Default review-first per the handoff.
    order_ingest_mode: str = "clientorder"

    # MASTER SAFETY GATE for the DIRECT catalog/pricing writes (bulk price & rename, promo,
    # single-article edits, pricing-rule apply, purchase-order create/close). False (default) =>
    # these endpoints compute + return a DRY-RUN and write NOTHING to Barsy, so testing
    # Ценообразуване or a bulk-rename pre-launch can't rewrite the real catalog. Flip to True
    # (BARSY_WRITE_LIVE=1) at launch. Reads are never gated. Mirrors webhooks_live / delivery_barsy_live.
    barsy_write_live: bool = False

    # MASTER SAFETY GATE for REAL courier waybills (Speedy/Econt create_shipment). False (default) =>
    # /orders/{id}/label and /orders/manual-label return a DRY-RUN plan and никакъв етикет се създава.
    # Deliberately SEPARATE from barsy_write_live: the courier is a different external system — a waybill
    # costs money the moment it's minted and the наложен платеж collects REAL cash from the customer, so a
    # pre-launch rehearsal of Етикет must never reach it. Flip to True (COURIER_LIVE=1) at launch, and only
    # together with BARSY_WRITE_LIVE — a real label without a booked sale means cash collected with no
    # Barsy продажба, no fiscal бон and no изписване (the router refuses that combination).
    courier_live: bool = False

    # --- Delivery intake (Приемане) -----------------------------------------------------
    # SAFETY GATE for the batch delivery→Barsy write (storeload create). False (default) =>
    # /intake/delivery/commit runs a DRY-RUN: it validates, creates nothing, and returns the exact
    # Barsy payload it WOULD send. Flip to True (DELIVERY_BARSY_LIVE=1) only once the Barsy
    # storeload API shape is confirmed (Hermes) and staff have trialled the flow. Mirrors webhooks_live.
    delivery_barsy_live: bool = False
    # SAFETY GATE for the COD→Barsy STORNO write: on an uncollected / refused / RTO delivery → storno the
    # Barsy sale (restores stock + reverses the fiscal бон). False (default) => the panel only records the
    # outcome locally and returns a dry-run plan. Flip to True (COD_BARSY_POST_LIVE=1) once the account
    # linkage + storno shape are trialled against a Barsy test object.
    # NOTE: under the paid-at-packing model the sale is already closed PAID at finalise (method 6,
    # print_receipt:1), so SETTLE is confirm-only — it posts NO payment to Barsy at all (posting one would
    # double-pay the account, which is exactly why that branch was removed). This flag gates only the storno.
    cod_barsy_post_live: bool = False
    # Barsy depot the delivery loads into. 1 = 'Основен', can_load=1 (verified live).
    barsy_depot_id: int = 1
    # Barsy currency for storeload/order prices. 1 = Евро/EUR (is_current on our install,
    # Currencies_getlist live). Prices are entered in EUR, so this stays 1.
    barsy_currency_id: int = 1
    # Storeloads price_mode: 1 = the delivery unit prices we send are VAT-INCLUSIVE (our prices are).
    # Polarity confirmed in storeloads/getlist.rst: "0 => без включено ДДС, 1 => с включено ДДС"
    # (the create.rst entry omits it, which earlier flagged this as unverified). 1 is correct.
    barsy_price_mode: int = 1
    # Barsy article min_profit_percent — a per-article floor guarding an in-store POS sale from going
    # below cost (0 = never below cost). Set on panel-created articles. ⚠️ Undocumented semantics —
    # verify against a Barsy test object before raising it above 0; writes are gated pre-launch.
    barsy_min_profit_percent: float = 0.0

    # --- Склад / store operations (Ревизия/Изписване/Прехвърляне/Зареждане/Сторно) ---------
    # SAFETY GATE for the panel's store-ops MUTATING writes: manual goods-load (Storeloads_create),
    # write-off (Storeouts_save), transfer (Storemoves_create), stocktake post (Revisions_*), and
    # document storno. False (default) => every store-ops write DRY-RUNS: it validates, writes nothing,
    # and returns the exact Barsy body it WOULD send. Flip to True (STORE_OPS_BARSY_LIVE=1) only after
    # the (partly undocumented) write shapes are trialled live against a test object with Hermes.
    store_ops_barsy_live: bool = False

    # --- Заявки email sending (Gmail SMTP) ---------------------------------------------
    # The panel sends a заявка to a supplier via the business Gmail. Set GMAIL_USER +
    # GMAIL_APP_PASSWORD (a Google App Password, NOT the account password) in ~/.hermes/.env.
    # Empty => /send-email refuses (not configured); export to CSV/Excel still works.
    gmail_user: str = ""
    gmail_app_password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    # display name + reply-to on the outbound заявка mail (defaults to gmail_user)
    order_from_name: str = "Дарлинг 7"

    # --- Assistant LLM backends (see assistant_llm.py) — all optional, read via _cfg() -----
    # Centralised here (pydantic-settings already reads ~/.hermes/.env) so there is ONE config
    # surface, not a second hand-rolled .env parser.
    assistant_backends: str = "free"       # comma list = try order
    claude_cli_path: str = ""
    assistant_claude_model: str = ""
    hermes_exe: str = ""
    anthropic_api_key: str = ""
    assistant_anthropic_key: str = ""
    assistant_anthropic_model: str = "claude-haiku-4-5"

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
