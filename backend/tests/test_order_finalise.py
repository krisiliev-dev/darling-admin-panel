# -*- coding: utf-8 -*-
"""Review → finalise / refuse endpoints (owner's clientorder model). The connector owns the Barsy/Shopify
write shapes (connector.finalise_order / refuse_order — added on the connector side); this router owns the
gate (BARSY_WRITE_LIVE), the confirm flag, the role check, and the audit line. We mock the two connector
functions so the test is panel-only."""
import app.routers.orders as orders_module

# id is a Shopify GID — the endpoint must pass the bare NUMERIC id to the connector (not the GID)
_ORDER = {"id": "gid://shopify/Order/1004", "name": "#1004"}


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


async def test_finalise_already_finalised_barsy_501_is_calm_200(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    # after a connector restart its in-process 'sale' memory is gone, so it re-calls Barsy, which refuses
    # with its own 501 (account already exists). No double decrement — surface it as a calm already_finalised.
    def boom(oid, do_write):
        raise RuntimeError("HTTP 501 on Accounts_createfromclientorder: по клиентска заявка 3 "
                           "вече има създадена/и сметка/и (8), покриващи изцяло сумата")
    monkeypatch.setattr(orders_module.connector, "finalise_order", boom, raising=False)
    r = await owner_client.post("/api/orders/1004/finalise", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "already_finalised"


async def test_finalise_other_barsy_error_still_502(owner_client, monkeypatch):
    _patch_order(monkeypatch)
    def boom(oid, do_write):
        raise RuntimeError("HTTP 500 on Accounts_createfromclientorder: вътрешна грешка")
    monkeypatch.setattr(orders_module.connector, "finalise_order", boom, raising=False)
    r = await owner_client.post("/api/orders/1004/finalise", json={"confirm": True})
    assert r.status_code == 502


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
