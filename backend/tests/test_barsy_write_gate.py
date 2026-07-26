# -*- coding: utf-8 -*-
"""The BARSY_WRITE_LIVE master gate for the panel's DIRECT catalog/pricing/purchasing writes.

When the gate is OFF (the production default, pre-launch), these endpoints must compute their
result but send NOTHING to Barsy — so an owner testing Ценообразуване or a bulk-rename can't
rewrite the live catalog. The suite runs with the gate ON (conftest), so here we flip it OFF
per-endpoint (patching the router's imported `barsy_write_live`) and assert:
  * the response is marked gated / dry-run, and
  * no mutating Barsy call was recorded.
"""
import pytest

from app.routers import catalog, pricing, purchasing


def _gate_off(monkeypatch, module):
    monkeypatch.setattr(module, "barsy_write_live", lambda: False)


@pytest.mark.asyncio
async def test_bulk_price_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    _gate_off(monkeypatch, catalog)
    # seed a cache row so there is a real change to (not) write
    from app.db import async_session_maker
    from app.models import ProductCache
    async with async_session_maker() as s:
        s.add(ProductCache(article_id=1, code="C1", name="Чаша", price=10.0))
        await s.commit()

    r = await owner_client.post("/api/catalog/bulk-price",
                                json={"article_ids": [1], "op": "set", "value": 5, "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gated"] is True and body["dry_run"] is True
    assert "Articles_update" not in barsy_recorder.operations   # nothing written to the ERP


@pytest.mark.asyncio
async def test_bulk_rename_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    _gate_off(monkeypatch, catalog)
    from app.db import async_session_maker
    from app.models import ProductCache
    async with async_session_maker() as s:
        s.add(ProductCache(article_id=2, code="C2", name="Стара", price=3.0,
                           shopify_category_id=None))
        await s.commit()
    r = await owner_client.post("/api/catalog/bulk-rename",
                                json={"article_ids": [2], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json().get("gated") is True
    assert "Articles_update" not in barsy_recorder.operations


@pytest.mark.asyncio
async def test_update_article_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    _gate_off(monkeypatch, catalog)
    r = await owner_client.post("/api/catalog/99",
                                json={"name": "Ново име", "description": "", "barcode": ""})
    assert r.status_code == 200, r.text
    assert r.json().get("gated") is True
    assert barsy_recorder.operations == []


@pytest.mark.asyncio
async def test_pricing_apply_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    _gate_off(monkeypatch, pricing)
    # Articles_get must return a cost so apply() reaches the write branch
    barsy_recorder.set_return("Articles_get", {"article_id": 5, "code": "C5",
                                               "delivery_price": "4.00", "actual_price": "8.00"})
    monkeypatch.setattr(pricing, "_supplier_for", lambda code: "АНИС")
    # a supplier markup so a price can be resolved
    from app.db import async_session_maker
    from app.models import PricingRule
    async with async_session_maker() as s:
        s.add(PricingRule(article_id=5, markup_pct=50.0))
        await s.commit()
    r = await owner_client.post("/api/pricing/apply", json={"article_id": 5, "confirm": True})
    # either gated dry-run (no write) or a 400 if no markup resolved — but never an Articles_update
    assert "Articles_update" not in barsy_recorder.operations
    if r.status_code == 200:
        assert r.json().get("gated") is True


@pytest.mark.asyncio
async def test_purchase_order_create_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    _gate_off(monkeypatch, purchasing)
    monkeypatch.setattr(purchasing, "_default_depot", lambda: 1)
    r = await owner_client.post("/api/purchasing/orders", json={
        "supplier_id": 6, "note": "тест", "close": False,
        "lines": [{"article_id": 1, "name": "Чаша", "amount": 3, "current_price": "2.00"}]})
    assert r.status_code in (200, 201), r.text
    assert r.json().get("gated") is True
    assert "Storeloads_create" not in barsy_recorder.operations
