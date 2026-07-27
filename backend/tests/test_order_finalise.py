# -*- coding: utf-8 -*-
"""Review → finalise / refuse endpoints (owner's clientorder model). The connector owns the Barsy/Shopify
write shapes (connector.finalise_order / refuse_order — added on the connector side); this router owns the
gate (BARSY_WRITE_LIVE), the confirm flag, the role check, and the audit line. We mock the two connector
functions so the test is panel-only."""
import app.routers.orders as orders_module

_ORDER = {"id": "1004", "name": "#1004"}


def _patch_order(monkeypatch):
    monkeypatch.setattr(orders_module, "_order_or_404", lambda oid: dict(_ORDER))


async def test_finalise_requires_confirm(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    r = await owner_client.post("/api/orders/1004/finalise", json={"confirm": False})
    assert r.status_code == 400


async def test_finalise_calls_connector_and_passes_write_gate(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    seen = {}

    def fake_finalise(oid, do_write):
        seen["oid"], seen["do_write"] = oid, do_write
        return {"status": "ok", "account_id": 8, "decremented": 1}

    monkeypatch.setattr(orders_module.connector, "finalise_order", fake_finalise, raising=False)
    r = await owner_client.post("/api/orders/1004/finalise", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert seen["oid"] == "1004"
    assert seen["do_write"] is True          # BARSY_WRITE_LIVE=1 in the test env
    assert r.json()["status"] == "ok" and r.json()["account_id"] == 8


async def test_refuse_calls_connector(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    monkeypatch.setattr(orders_module.connector, "refuse_order",
                        lambda oid, do_write: {"status": "ok", "voided_clientorder": 3}, raising=False)
    r = await owner_client.post("/api/orders/1004/refuse", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["voided_clientorder"] == 3


async def test_finalise_missing_connector_fn_is_501_not_500(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    # if the connector hasn't exposed finalise_order yet, we return a clean 501, not a 500
    monkeypatch.delattr(orders_module.connector, "finalise_order", raising=False)
    r = await owner_client.post("/api/orders/1004/finalise", json={"confirm": True})
    assert r.status_code == 501


async def test_finalise_refused_for_packer(packer_client, monkeypatch):
    _patch_order(monkeypatch)
    monkeypatch.setattr(orders_module.connector, "finalise_order",
                        lambda oid, do_write: {"status": "ok"}, raising=False)
    r = await packer_client.post("/api/orders/1004/finalise", json={"confirm": True})
    assert r.status_code in (401, 403)   # packer isn't a review role
