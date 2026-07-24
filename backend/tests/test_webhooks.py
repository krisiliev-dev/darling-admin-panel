# -*- coding: utf-8 -*-
"""Barsy⇄Shopify webhook receivers: signature/token gating, the webhooks_live write-gate, and
topic/code dispatch. The connector itself is mocked — we assert what WOULD be called, and crucially
that do_write is False unless webhooks_live is on."""
import base64
import hashlib
import hmac
import json
import types

import pytest
import pytest_asyncio

from app.routers import webhooks

_SECRET = "test-shopify-secret"
_TOKEN = "test-barsy-token"


def _sign(raw: bytes) -> str:
    return base64.b64encode(hmac.new(_SECRET.encode(), raw, hashlib.sha256).digest()).decode()


@pytest_asyncio.fixture
def spy(monkeypatch):
    """Record every connector call the receivers make, as (fn_name, args, kwargs)."""
    calls = []

    def rec(name):
        def _fn(*a, **k):
            calls.append((name, a, k))
            return {"ok": True}
        return _fn

    for fn in ("shopify_order_to_barsy", "shopify_order_cancellation_to_barsy",
               "shopify_refund_to_barsy", "shopify_fulfillment_to_barsy",
               "shopify_customer_to_barsy", "barsy_article_to_shopify"):
        monkeypatch.setattr(webhooks.connector, fn, rec(fn))
    return calls


def _set_live(monkeypatch, live: bool):
    monkeypatch.setattr(webhooks, "_live", lambda: live)


# --- Shopify HMAC gating ------------------------------------------------------------
async def test_shopify_rejects_bad_signature(client, spy):
    r = await client.post("/webhooks/shopify/orders",
                          content=b'{"id":1}',
                          headers={"x-shopify-hmac-sha256": "wrong", "x-shopify-topic": "orders/paid"})
    assert r.status_code == 401
    assert not spy  # nothing dispatched


async def test_shopify_rejects_missing_signature(client, spy):
    r = await client.post("/webhooks/shopify/orders", content=b'{"id":1}',
                          headers={"x-shopify-topic": "orders/paid"})
    assert r.status_code == 401


async def test_shopify_valid_signature_dispatches_dry_run(client, spy, monkeypatch):
    _set_live(monkeypatch, False)  # default gate: verify + plan, but do_write must be False
    raw = json.dumps({"id": 123, "name": "#1001"}).encode()
    r = await client.post("/webhooks/shopify/orders", content=raw,
                          headers={"x-shopify-hmac-sha256": _sign(raw), "x-shopify-topic": "orders/paid"})
    assert r.status_code == 200
    assert r.json()["live"] is False
    assert len(spy) == 1
    name, _a, kw = spy[0]
    assert name == "shopify_order_to_barsy"
    assert kw.get("do_write") is False  # THE write-gate holds


async def test_shopify_live_gate_passes_do_write_true(client, spy, monkeypatch):
    _set_live(monkeypatch, True)
    raw = json.dumps({"id": 9}).encode()
    r = await client.post("/webhooks/shopify/orders", content=raw,
                          headers={"x-shopify-hmac-sha256": _sign(raw), "x-shopify-topic": "orders/paid"})
    assert r.status_code == 200
    assert spy[0][2].get("do_write") is True


async def test_shopify_topic_routing(client, spy, monkeypatch):
    _set_live(monkeypatch, False)
    cases = {
        "orders/cancelled": "shopify_order_cancellation_to_barsy",
        "refunds/create": "shopify_refund_to_barsy",
        "fulfillments/create": "shopify_fulfillment_to_barsy",
        "customers/create": "shopify_customer_to_barsy",
    }
    for topic, expected_fn in cases.items():
        spy.clear()
        raw = json.dumps({"id": 1}).encode()
        r = await client.post("/webhooks/shopify/orders", content=raw,
                              headers={"x-shopify-hmac-sha256": _sign(raw), "x-shopify-topic": topic})
        assert r.status_code == 200
        assert spy and spy[0][0] == expected_fn, f"{topic} -> {spy}"


# --- Barsy token gating -------------------------------------------------------------
async def test_barsy_rejects_bad_token(client, spy):
    r = await client.post("/webhooks/barsy/article?token=nope", json={"code": "SKU1"})
    assert r.status_code == 401
    assert not spy


async def test_barsy_valid_token_dispatches(client, spy, monkeypatch):
    _set_live(monkeypatch, False)
    r = await client.post(f"/webhooks/barsy/article?token={_TOKEN}", json={"code": "SKU1"})
    assert r.status_code == 200
    assert spy and spy[0][0] == "barsy_article_to_shopify"
    assert spy[0][1][0] == "SKU1"  # code extracted


async def test_barsy_registration_probe_returns_200(client, spy, monkeypatch):
    _set_live(monkeypatch, False)
    # a token-valid but article-less probe (Barsy validates the URL on subscribe) still 200s,
    # and dispatches nothing
    r = await client.post(f"/webhooks/barsy/article?token={_TOKEN}", json={})
    assert r.status_code == 200
    assert not spy


def test_barsy_code_extraction_variants():
    assert webhooks._barsy_code({"code": "A"}) == "A"
    assert webhooks._barsy_code({"sku": "B"}) == "B"
    assert webhooks._barsy_code({"article": {"barcode": "C"}}) == "C"
    assert webhooks._barsy_code({"nope": 1}) == ""


# --- reconcile is owner-gated -------------------------------------------------------
async def test_reconcile_requires_owner(client):
    r = await client.post("/webhooks/reconcile")
    assert r.status_code in (401, 403)
