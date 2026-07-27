# -*- coding: utf-8 -*-
"""Кошници — Shopify checkout webhooks → read-only carts mirror + /api/carts. The checkout topics are
HMAC-verified but NOT gated by webhooks_live (they never touch Barsy — panel display only)."""
import base64
import hashlib
import hmac
import json

from sqlalchemy import select

from app.db import async_session_maker
from app.models import Checkout

_SECRET = "test-shopify-secret"


def _sign(raw: bytes) -> str:
    return base64.b64encode(hmac.new(_SECRET.encode(), raw, hashlib.sha256).digest()).decode()


async def _post(client, topic, payload):
    raw = json.dumps(payload).encode()
    return await client.post("/webhooks/shopify/orders", content=raw,
                             headers={"x-shopify-hmac-sha256": _sign(raw), "x-shopify-topic": topic})


async def test_checkout_create_appears_as_active_cart(client, owner_client):
    r = await _post(client, "checkouts/create", {
        "token": "chk_1", "email": "a@b.bg", "total_price": "42.00", "currency": "BGN",
        "line_items": [{"quantity": 2}, {"quantity": 1}]})
    assert r.status_code == 200
    async with async_session_maker() as s:
        row = await s.get(Checkout, "chk_1")
    assert row and row.item_count == 3 and row.completed is False and row.email == "a@b.bg"
    body = (await owner_client.get("/api/carts")).json()
    assert body["active_count"] == 1 and body["active"][0]["token"] == "chk_1"


async def test_checkout_completed_drops_out_of_active(client, owner_client):
    await _post(client, "checkouts/create", {"token": "chk_2", "line_items": [{"quantity": 1}]})
    r = await _post(client, "checkouts/update", {"token": "chk_2", "completed_at": "2026-07-27T10:00:00Z"})
    assert r.status_code == 200
    async with async_session_maker() as s:
        row = await s.get(Checkout, "chk_2")
    assert row.completed is True
    assert (await owner_client.get("/api/carts")).json()["active_count"] == 0


async def test_checkout_delete_removes_it(client):
    await _post(client, "checkouts/create", {"token": "chk_3", "line_items": [{"quantity": 1}]})
    await _post(client, "checkouts/delete", {"token": "chk_3"})
    async with async_session_maker() as s:
        assert await s.get(Checkout, "chk_3") is None


async def test_checkout_is_not_gated_by_webhooks_live(client, monkeypatch):
    # cart data is panel-only → it records even with the Barsy write-gate OFF (default)
    from app.routers import webhooks
    monkeypatch.setattr(webhooks, "_live", lambda: False)
    r = await _post(client, "checkouts/create", {"token": "chk_4", "line_items": [{"quantity": 5}]})
    assert r.status_code == 200
    async with async_session_maker() as s:
        assert (await s.get(Checkout, "chk_4")).item_count == 5


async def test_checkout_bad_signature_rejected(client):
    raw = json.dumps({"token": "x"}).encode()
    r = await client.post("/webhooks/shopify/orders", content=raw,
                          headers={"x-shopify-hmac-sha256": "wrong", "x-shopify-topic": "checkouts/create"})
    assert r.status_code == 401
    async with async_session_maker() as s:
        assert (await s.execute(select(Checkout))).scalars().first() is None   # nothing recorded


async def test_carts_endpoint_requires_auth(client):
    assert (await client.get("/api/carts")).status_code == 401
