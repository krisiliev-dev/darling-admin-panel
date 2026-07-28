# -*- coding: utf-8 -*-
"""Ship / dispatch endpoint (#83). The connector owns the Shopify fulfillmentCreate shape
(connector.ship_order); this router owns the gate (BARSY_WRITE_LIVE), confirm, role, and audit. We mock
the connector fn so the test is panel-only."""
import app.routers.orders as orders_module

_ORDER = {"id": "gid://shopify/Order/1006", "name": "#1006"}


def _patch_order(monkeypatch):
    monkeypatch.setattr(orders_module, "_order_or_404", lambda oid: dict(_ORDER))


async def test_ship_requires_confirm(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    r = await owner_client.post("/api/orders/1006/ship", json={"confirm": False})
    assert r.status_code == 400


async def test_ship_passes_numeric_id_and_tracking_and_write_gate(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    seen = {}

    def fake_ship(oid, tracking, do_write):
        seen.update(oid=oid, tracking=tracking, do_write=do_write)
        return {"status": "ok", "order_ref": "#1006", "fulfillment_orders": ["gid://shopify/FulfillmentOrder/1"]}

    monkeypatch.setattr(orders_module.connector, "ship_order", fake_ship, raising=False)
    r = await owner_client.post("/api/orders/1006/ship", json={"confirm": True, "tracking": "TESTTRACK1"})
    assert r.status_code == 200, r.text
    assert seen["oid"] == "1006"                 # numeric, not the GID
    assert seen["tracking"] == "TESTTRACK1"
    assert seen["do_write"] is True              # BARSY_WRITE_LIVE=1 in the test env
    assert r.json()["status"] == "ok"


async def test_ship_empty_tracking_becomes_none(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    seen = {}
    monkeypatch.setattr(orders_module.connector, "ship_order",
                        lambda oid, tracking, do_write: seen.update(tracking=tracking) or {"status": "ok"},
                        raising=False)
    r = await owner_client.post("/api/orders/1006/ship", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert seen["tracking"] is None              # "" → None so the connector omits trackingInfo


async def test_ship_missing_connector_fn_is_501(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    monkeypatch.delattr(orders_module.connector, "ship_order", raising=False)
    r = await owner_client.post("/api/orders/1006/ship", json={"confirm": True})
    assert r.status_code == 501


async def test_ship_allowed_for_packer(packer_client, monkeypatch):
    _patch_order(monkeypatch)
    monkeypatch.setattr(orders_module.connector, "ship_order",
                        lambda oid, tracking, do_write: {"status": "ok"}, raising=False)
    r = await packer_client.post("/api/orders/1006/ship", json={"confirm": True})
    assert r.status_code == 200, r.text   # packer ships (they create labels)
