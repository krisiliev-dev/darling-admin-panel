# -*- coding: utf-8 -*-
"""Barsy ⇄ Shopify webhook RECEIVERS — the HTTP hosting layer over the connector logic in
agent-pipeline/barsy_shopify_connector.py (see admin-panel/HANDOFF_connector.md).

These endpoints are called by Shopify / Barsy, NOT by logged-in panel users, so they carry no
session auth. Their gate is cryptographic instead:
  • Shopify → HMAC-SHA256(raw_body, app_secret), constant-time compared to X-Shopify-Hmac-Sha256.
  • Barsy   → a shared secret token embedded in the registered URL (Barsy doesn't sign payloads).

MASTER SAFETY GATE: settings.webhooks_live. While False (default), a verified webhook is parsed and
a dry-run PLAN is logged, but NOTHING is written to Barsy/Shopify. Flip WEBHOOKS_LIVE=1 only at store
launch (fiscal POS configured, stock loaded). This is why the receivers are safe to deploy early —
they authenticate and observe, but stay read-only until deliberately switched on.

We verify synchronously, then hand the (potentially slow) Barsy/Shopify work to a BackgroundTask and
return 200 immediately — Shopify retries on any non-2xx, so we must ack fast.
"""
import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..core import connector
from ..users import current_owner
from ..models import User

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("darling.webhooks")


def _live() -> bool:
    return get_settings().webhooks_live


# --- Shopify → Barsy -----------------------------------------------------------------
def _verify_shopify(raw: bytes, header_hmac: str | None) -> bool:
    """Constant-time HMAC-SHA256 check of the RAW body against the app secret."""
    secret = get_settings().shopify_webhook_secret
    if not secret or not header_hmac:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, header_hmac)


def _dispatch_shopify(topic: str, payload: dict, live: bool):
    """Route a Shopify topic to the matching connector translation. Returns the plan/result dict."""
    mode = get_settings().order_ingest_mode
    if topic in ("orders/paid", "orders/create"):
        return connector.shopify_order_to_barsy(payload, mode=mode, do_write=live)
    if topic == "orders/cancelled":
        return connector.shopify_order_cancellation_to_barsy(payload, do_write=live)
    if topic == "refunds/create":
        return connector.shopify_refund_to_barsy(payload, do_write=live)
    if topic == "fulfillments/create":
        return connector.shopify_fulfillment_to_barsy(payload, do_write=live)
    if topic in ("customers/create", "customers/update"):
        return connector.shopify_customer_to_barsy(payload, do_write=live)
    return {"skipped": f"unhandled topic {topic}"}


async def _process_shopify(topic: str, payload: dict, live: bool):
    try:
        result = await run_in_threadpool(_dispatch_shopify, topic, payload, live)
        # surface unmatched SKUs loudly — the handoff says never drop them silently
        missing = (result or {}).get("missing_skus")
        if missing:
            log.warning("Shopify %s: unmatched SKUs %s", topic, missing)
        log.info("Shopify %s processed (live=%s): %s", topic, live,
                 {k: v for k, v in (result or {}).items() if k != "lines"})
    except Exception as e:  # never let a handler crash take down anything; we already 200'd
        log.exception("Shopify %s handler failed: %s", topic, e)


@router.post("/shopify/orders")
async def shopify_receiver(request: Request, background: BackgroundTasks):
    """Generic Shopify receiver — register every order-related topic to this URL; we dispatch by the
    authoritative X-Shopify-Topic header. Verifies HMAC on the raw body, then processes async."""
    raw = await request.body()
    if not _verify_shopify(raw, request.headers.get("x-shopify-hmac-sha256")):
        # 401 (not 500) so an attacker can't tell configured-vs-misconfigured apart
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature.")
    topic = request.headers.get("x-shopify-topic", "")
    try:
        import json
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed JSON.")
    background.add_task(_process_shopify, topic, payload, _live())
    return {"ok": True, "topic": topic, "live": _live()}


# --- Barsy → Shopify -----------------------------------------------------------------
def _verify_barsy_token(request: Request) -> bool:
    """Barsy doesn't sign; we require the shared token we baked into the registered URL. Accept it
    from ?token= or an X-Darling-Token header, constant-time compared."""
    expected = get_settings().barsy_webhook_token
    if not expected:
        return False
    supplied = request.query_params.get("token") or request.headers.get("x-darling-token") or ""
    return hmac.compare_digest(expected, supplied)


def _barsy_code(payload) -> str:
    """Best-effort extract the changed article's code from Barsy's webhook payload (shape varies)."""
    if isinstance(payload, dict):
        for k in ("code", "article_code", "sku", "barcode"):
            v = payload.get(k)
            if v:
                return str(v)
        art = payload.get("article") or payload.get("data")
        if isinstance(art, dict):
            return _barsy_code(art)
    return ""


async def _process_barsy_article(code: str, live: bool):
    """ONE fan-out for a Barsy article.updated event: (a) refresh the local product cache row so
    search/filter stay fresh, (b) mirror name/price to Shopify. Panel owns the single subscription."""
    # (a) refresh the local product cache — always (independent of webhooks_live)
    try:
        from .catalog import refresh_cache_for_code
        ok = await refresh_cache_for_code(code)
        log.info("Barsy article %s -> cache refresh: %s", code, ok)
    except Exception as e:
        log.exception("Barsy article %s cache refresh failed: %s", code, e)
    # (b) mirror to Shopify (dry-run unless webhooks_live)
    try:
        result = await run_in_threadpool(connector.barsy_article_to_shopify, code, live)
        log.info("Barsy article %s -> Shopify (live=%s): %s", code, live, result)
    except Exception as e:
        log.exception("Barsy article %s Shopify mirror failed: %s", code, e)


@router.post("/barsy/article")
async def barsy_article_receiver(request: Request, background: BackgroundTasks):
    """Barsy article-changed webhook → mirror name/price onto Shopify. Barsy sends a test call on
    registration and needs a 2xx to validate, so a tokenless/empty probe still returns 200 (but we
    only process real, token-authenticated payloads)."""
    if not _verify_barsy_token(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token.")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    code = _barsy_code(payload)
    if code:
        background.add_task(_process_barsy_article, code, _live())
    else:
        log.info("Barsy webhook: no article code in payload (likely a registration probe).")
    return {"ok": True, "code": code, "live": _live()}


# --- operator tools (owner-gated) ----------------------------------------------------
@router.post("/reconcile")
async def run_reconcile(
    limit: int = 200,
    actor: User = Depends(current_owner),
):
    """Manual drift sweep (Barsy vs Shopify). Dry-run unless webhooks_live is on — mirrors the
    nightly job. Owner-gated because it can (when live) push fixes to Shopify."""
    result = await run_in_threadpool(connector.reconcile, limit, _live())
    log.info("reconcile run by %s (live=%s): checked=%s drift=%s",
             actor.email, _live(), result.get("checked"), len(result.get("drift", [])))
    return result
