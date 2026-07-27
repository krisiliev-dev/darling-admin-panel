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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import connector
from ..issues import note_exception, note_shopify_result
from ..models import User
from ..users import current_owner

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
    """Route a Shopify topic to the matching connector translation. Returns the plan/result dict.
    (GDPR topics are handled separately/async in _process_shopify — they never reach here.)"""
    mode = get_settings().order_ingest_mode
    # orders/updated → re-run the order mapping; the connector's _seen_order dedupe makes it a no-op
    # for an already-booked order, and a catch-up if we somehow missed create/paid.
    if topic in ("orders/paid", "orders/create", "orders/updated"):
        return connector.shopify_order_to_barsy(payload, mode=mode, do_write=live)
    if topic == "orders/cancelled":
        return connector.shopify_order_cancellation_to_barsy(payload, do_write=live)
    if topic == "refunds/create":
        return connector.shopify_refund_to_barsy(payload, do_write=live)
    if topic in ("fulfillments/create", "fulfillments/update"):
        return connector.shopify_fulfillment_to_barsy(payload, do_write=live)
    if topic in ("customers/create", "customers/update"):
        return connector.shopify_customer_to_barsy(payload, do_write=live)
    return {"skipped": f"unhandled topic {topic}"}


# Shopify's mandatory GDPR/compliance topics. Handled panel-side (our PII footprint = the phone-keyed
# OrderOutcome refusal history + any nekorekten reports); Shopify remains the customer system of record.
# These act on OUR OWN data as a legal duty, so they are NOT behind the webhooks_live write-gate.
_GDPR_TOPICS = {"customers/data_request", "customers/redact", "shop/redact"}


def _shopify_customer_phone(payload: dict) -> str:
    c = payload.get("customer") if isinstance(payload, dict) else None
    return (c or {}).get("phone") or ""


async def _process_gdpr(topic: str, payload: dict):
    """Honour a Shopify GDPR webhook against the panel's own data. data_request → compile + audit (owner
    fulfils); customers/redact → erase our PII + retract nekorekten; shop/redact → audit (single-merchant)."""
    from ..db import async_session_maker
    from .gdpr import collect_customer_data, erase_customer_data
    phone = _shopify_customer_phone(payload)
    async with async_session_maker() as session:
        if topic == "customers/redact":
            if phone:
                res = await erase_customer_data(session, phone, actor=None)
                log.info("GDPR customers/redact %s -> %s", phone, res)
            else:
                await log_action(session, None, "gdpr.redact.no_phone",
                                 detail=str((payload.get("customer") or {}).get("id")))
        elif topic == "customers/data_request":
            data = await collect_customer_data(session, phone) if phone else {"count": 0}
            await log_action(session, None, "gdpr.data_request", target=phone,
                             detail=f"records={data.get('count', 0)}")
            log.info("GDPR customers/data_request %s -> %s records (owner fulfils)", phone, data.get("count"))
        elif topic == "shop/redact":
            await log_action(session, None, "gdpr.shop_redact", detail=str(payload.get("shop_domain")))


_CHECKOUT_TOPICS = {"checkouts/create", "checkouts/update", "checkouts/delete"}


async def _process_checkout(topic: str, payload: dict):
    """Upsert a Shopify checkout into the panel's READ-ONLY carts mirror (never touches Barsy) — this
    powers the 'active / abandoned carts' view. A checkout with completed_at became an order → marked
    completed (drops out of 'active'); a delete removes it."""
    from sqlalchemy import delete as _delete

    from ..db import async_session_maker
    from ..models import Checkout
    token = str(payload.get("token") or payload.get("id") or "").strip()
    if not token:
        return
    async with async_session_maker() as session:
        if topic == "checkouts/delete":
            await session.execute(_delete(Checkout).where(Checkout.token == token))
            await session.commit()
            return
        cust = payload.get("customer") or {}
        li = payload.get("line_items") or []
        vals = {
            "email": payload.get("email") or cust.get("email") or "",
            "phone": payload.get("phone") or cust.get("phone") or "",
            "name": str(payload.get("name") or ""),
            "total_price": str(payload.get("total_price") or ""),
            "currency": payload.get("currency") or payload.get("presentment_currency") or "",
            "item_count": sum(int(x.get("quantity") or 0) for x in li) or len(li),
            "completed": bool(payload.get("completed_at")),
        }
        row = await session.get(Checkout, token)
        if row:
            for k, v in vals.items():
                setattr(row, k, v)
        else:
            session.add(Checkout(token=token, **vals))
        await session.commit()


async def _process_shopify(topic: str, payload: dict, live: bool):
    # GDPR/compliance topics act on our OWN data (legal duty) — handled async here, never gated.
    if topic in _GDPR_TOPICS:
        try:
            await _process_gdpr(topic, payload)
        except Exception as e:  # noqa: BLE001 — already 200'd; never let a handler crash propagate
            log.exception("GDPR %s handler failed: %s", topic, e)
        return
    # Checkout/cart topics are READ-ONLY panel data (active + abandoned carts) — never gated, never
    # touch Barsy; only the webhooks_live gate applies to the Barsy-writing order path below.
    if topic in _CHECKOUT_TOPICS:
        try:
            await _process_checkout(topic, payload)
        except Exception as e:  # noqa: BLE001 — already 200'd
            log.exception("Checkout %s handler failed: %s", topic, e)
        return
    try:
        result = await run_in_threadpool(_dispatch_shopify, topic, payload, live)
        # NEVER silently swallow a refusal/anomaly — persist it as a panel ProcessingIssue (nav badge +
        # Home strip + Проблеми page) so staff see it. A clean result records nothing.
        recorded = await note_shopify_result(topic, payload, result)
        log.info("Shopify %s processed (live=%s, issue=%s): %s", topic, live, recorded,
                 {k: v for k, v in (result or {}).items() if k != "lines"})
    except Exception as e:  # never let a handler crash take down anything; we already 200'd
        log.exception("Shopify %s handler failed: %s", topic, e)
        await note_exception("shopify", topic, payload, e)


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


@router.head("/barsy/article")
async def barsy_article_probe() -> Response:
    """Barsy's webhook-registration probe sends an HTTP HEAD and accepts 200–299/401/403/405/500/501.
    Starlette's default 405 for HEAD-on-a-POST-route sets Content-Length to the JSON error body's size
    but (correctly) sends no body on HEAD → the length/body mismatch makes strict clients (Barsy) fail
    with 'transfer closed with N bytes remaining'. An explicit empty 200 gives a clean, self-consistent
    response (Content-Length: 0) that the probe validates."""
    return Response(status_code=200)


@router.post("/barsy/article")
async def barsy_article_receiver(request: Request, background: BackgroundTasks):
    """Barsy article-changed webhook (POST) → refresh the local product cache AND mirror name/price onto
    Shopify (see _process_barsy_article). Requires the shared token (else 401). NOTE: Barsy's registration
    probe is a HEAD (handled by barsy_article_probe above → clean empty 200, no token needed); this POST
    path is the real event delivery and IS token-gated. A token-valid POST with no article code (e.g. a
    manual test) still 200s but processes nothing."""
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
