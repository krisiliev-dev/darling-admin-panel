# -*- coding: utf-8 -*-
"""Поръчки (/api/orders) — courier readiness, office lookup, order queue, quote, label
creation, manual shipments, tracking and status updates.

No real courier / Shopify is hit: we monkeypatch app.routers.orders.courier_mod.get (per the
brief) plus courier_mod.available and the shopify_orders helpers. Label/manual-label are real,
irreversible actions, so the focus is their gating: confirm flag, role, double-label guard,
and the 502 path when the courier raises.
"""

import app.routers.orders as orders


# ------------------------------------------------------------------ fakes ----
class FakeCourier:
    def __init__(self, key="speedy", raise_on=None):
        self.key = key
        self.raise_on = raise_on or set()

    def ping(self):
        return {"sender_name": "Дарлинг", "sender_place": "София"}

    def offices(self, city=None):
        offices = [{"id": "1", "name": "Офис 1", "city": "София"},
                   {"id": "2", "name": "Офис 2", "city": "Пловдив"}]
        if city:
            offices = [o for o in offices if o["city"] == city]
        return offices

    def calculate(self, shp):
        if "calculate" in self.raise_on:
            raise RuntimeError("тарифата недостъпна")
        return {"total": 5.90, "currency": "EUR"}

    def create_shipment(self, shp, do_write=False):
        if "create" in self.raise_on:
            raise RuntimeError("куриерът отказа")
        return {"tracking_number": "TRK123", "label_ref": "LBL1", "cod_amount": shp.cod_amount}

    def track(self, tracking):
        return {"tracking": tracking, "status": "in_transit"}


_OID = "1001"   # kept free of '#' and '/' so it is path-safe in the test URLs
_ORDER = {"id": _OID, "name": "#1001", "customer": "Иван", "phone": "0888123456",
          "city": "София", "zip": "1000", "address": "ул. Х 1",
          "cod_amount": 40.0, "total": 55.0, "items": []}


def _patch_courier(monkeypatch, courier):
    monkeypatch.setattr(orders.courier_mod, "get", lambda key: courier)


def _patch_orders(monkeypatch, orders_list=None, state=None):
    orders_list = orders_list if orders_list is not None else [_ORDER]
    state = state or {}
    monkeypatch.setattr(orders.shopify_orders, "fetch_orders",
                        lambda limit=100, unfulfilled_only=False: list(orders_list))
    monkeypatch.setattr(orders.shopify_orders, "state_map", lambda: dict(state))
    recorded = {}
    monkeypatch.setattr(orders.shopify_orders, "set_state",
                        lambda oid, oname, **f: recorded.update({oid: {"name": oname, **f}}))
    # label = dispatch, which is guarded by _order_finalised (finalise-before-ship). These tests cover the
    # label flow itself, so treat the order as finalised; the guard has dedicated tests in test_order_ship.
    monkeypatch.setattr(orders.connector, "finalise_order",
                        lambda oid, do_write: {"status": "duplicate", "reason": "already_finalised"},
                        raising=False)
    return recorded


# ---------------------------------------------------------- GET /couriers ----
async def test_courier_readiness_configured_and_ready(staff_client, monkeypatch):
    monkeypatch.setattr(orders.courier_mod, "available",
                        lambda: [("speedy", "Спиди", True), ("boxnow", "BoxNow", False)])
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.get("/api/orders/couriers")
    assert r.status_code == 200, r.text
    rows = {c["key"]: c for c in r.json()["couriers"]}
    assert rows["speedy"]["ready"] is True
    assert rows["speedy"]["sender"] == "Дарлинг"
    assert rows["boxnow"]["configured"] is False and rows["boxnow"]["ready"] is False


async def test_courier_readiness_econt_without_sender_not_ready(staff_client, monkeypatch):
    class EcontNoSender(FakeCourier):
        def ping(self):
            return {"sender_name": "", "sender_configured": False}
    monkeypatch.setattr(orders.courier_mod, "available", lambda: [("econt", "Еконт", True)])
    _patch_courier(monkeypatch, EcontNoSender("econt"))
    r = await staff_client.get("/api/orders/couriers")
    row = r.json()["couriers"][0]
    assert row["ready"] is False
    assert "подателя" in row["note"]


async def test_couriers_requires_auth(client):
    r = await client.get("/api/orders/couriers")
    assert r.status_code == 401


# --------------------------------------------------- GET /couriers/{k}/offices ----
async def test_courier_offices_filter(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.get("/api/orders/couriers/speedy/offices", params={"city": "Пловдив"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["courier"] == "speedy" and body["count"] == 1
    assert body["offices"][0]["city"] == "Пловдив"


async def test_courier_offices_upstream_502(staff_client, monkeypatch):
    class Boom(FakeCourier):
        def offices(self, city=None):
            raise RuntimeError("еконт недостъпен")
    _patch_courier(monkeypatch, Boom())
    r = await staff_client.get("/api/orders/couriers/econt/offices")
    assert r.status_code == 502


# ----------------------------------------------------------------- GET / ----
async def test_list_orders_merges_op_status(staff_client, monkeypatch):
    _patch_orders(monkeypatch, orders_list=[_ORDER],
                  state={"1001": {"status": "packed", "courier": "speedy",
                                     "tracking": "T", "label_ref": "L"}})
    r = await staff_client.get("/api/orders")
    assert r.status_code == 200, r.text
    o = r.json()["orders"][0]
    assert o["op_status"] == "packed"
    assert o["op_status_bg"] == "опакована"
    assert o["courier"] == "speedy"


async def test_list_orders_shopify_down_is_graceful(staff_client, monkeypatch):
    def boom(limit=100, unfulfilled_only=False):
        raise RuntimeError("shopify 503")
    monkeypatch.setattr(orders.shopify_orders, "fetch_orders", boom)
    r = await staff_client.get("/api/orders")
    assert r.status_code == 200
    body = r.json()
    assert body["orders"] == []
    assert "Shopify недостъпен" in body["warning"]


# ------------------------------------------------------- POST /{id}/quote ----
async def test_quote_is_side_effect_free(staff_client, monkeypatch):
    _patch_orders(monkeypatch)
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.post("/api/orders/1001/quote",
                                json={"courier": "speedy", "weight_kg": 2.0})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 5.90


async def test_quote_unknown_order_is_404(staff_client, monkeypatch):
    _patch_orders(monkeypatch, orders_list=[])
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/9999/quote", json={"courier": "speedy"})
    assert r.status_code == 404


async def test_quote_requires_auth(client):
    r = await client.post("/api/orders/1001/quote", json={"courier": "speedy"})
    assert r.status_code == 401


# ------------------------------------------------------- POST /{id}/label ----
async def test_label_without_confirm_is_400(staff_client, monkeypatch):
    _patch_orders(monkeypatch)
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/1001/label",
                                json={"courier": "speedy", "confirm": False})
    assert r.status_code == 400


async def test_label_happy_path_marks_shipped(staff_client, monkeypatch):
    recorded = _patch_orders(monkeypatch)
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.post("/api/orders/1001/label",
                                json={"courier": "speedy", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["tracking_number"] == "TRK123"
    # final persisted state for the order is 'shipped' with the tracking number
    assert recorded["1001"]["status"] == "shipped"
    assert recorded["1001"]["tracking"] == "TRK123"


async def test_label_blocked_when_not_finalised_is_409(staff_client, monkeypatch):
    # finalise-before-ship: labelling an un-decremented order would fulfil it and desync stock, so block
    # BEFORE the courier is even called (the FakeCourier here raises if hit, proving we never reach it).
    _patch_orders(monkeypatch)
    monkeypatch.setattr(orders.connector, "finalise_order",
                        lambda oid, do_write: {"status": "ok", "dry_run": True}, raising=False)  # NOT finalised
    _patch_courier(monkeypatch, FakeCourier(raise_on={"create"}))
    r = await staff_client.post("/api/orders/1001/label",
                                json={"courier": "speedy", "confirm": True})
    assert r.status_code == 409
    assert "Финализирай" in r.json()["detail"]


async def test_label_double_guard_is_409(staff_client, monkeypatch):
    _patch_orders(monkeypatch, state={"1001": {"label_ref": "already", "status": "shipped"}})
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/1001/label",
                                json={"courier": "speedy", "confirm": True})
    assert r.status_code == 409


async def test_label_courier_failure_is_502_and_releases_claim(staff_client, monkeypatch):
    recorded = _patch_orders(monkeypatch)
    _patch_courier(monkeypatch, FakeCourier(raise_on={"create"}))
    r = await staff_client.post("/api/orders/1001/label",
                                json={"courier": "speedy", "confirm": True})
    assert r.status_code == 502
    # the transient 'labeling' claim is released back to 'new' so a retry can proceed
    assert recorded["1001"]["status"] == "new"


async def test_label_requires_auth(client):
    # label creation is role-gated (staff/packer); an unauthenticated caller is refused
    # before any courier/Shopify contact. (packer IS allowed, so there is no lower role to 403.)
    r = await client.post("/api/orders/1001/label", json={"courier": "speedy", "confirm": True})
    assert r.status_code == 401


# ------------------------------------------------- POST /manual-quote|label ----
async def test_manual_quote(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.post("/api/orders/manual-quote", json={
        "courier": "speedy", "recipient": "Иван", "phone": "0888", "city": "София",
        "weight_kg": 1.5})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 5.90


async def test_manual_label_without_confirm_is_400(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/manual-label", json={
        "courier": "speedy", "recipient": "Иван", "phone": "0888", "confirm": False})
    assert r.status_code == 400


async def test_manual_label_missing_recipient_is_400(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/manual-label", json={
        "courier": "speedy", "recipient": "  ", "phone": "0888", "confirm": True})
    assert r.status_code == 400


async def test_manual_label_happy_path(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.post("/api/orders/manual-label", json={
        "courier": "speedy", "recipient": "Иван", "phone": "0888",
        "cod_amount": 30.0, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["tracking_number"] == "TRK123"


async def test_manual_label_courier_failure_is_502(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier(raise_on={"create"}))
    r = await staff_client.post("/api/orders/manual-label", json={
        "courier": "speedy", "recipient": "Иван", "phone": "0888", "confirm": True})
    assert r.status_code == 502


async def test_manual_label_negative_cod_is_422(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier())
    r = await staff_client.post("/api/orders/manual-label", json={
        "courier": "speedy", "recipient": "Иван", "phone": "0888",
        "cod_amount": -5, "confirm": True})
    assert r.status_code == 422


# --------------------------------------------------------- GET /track + status ----
async def test_track_passthrough(staff_client, monkeypatch):
    _patch_courier(monkeypatch, FakeCourier("speedy"))
    r = await staff_client.get("/api/orders/track/speedy/TRK123")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_transit"


async def test_set_status_valid(staff_client, monkeypatch):
    monkeypatch.setattr(orders.shopify_orders, "set_state", lambda *a, **k: None)
    r = await staff_client.post("/api/orders/status", json={
        "order_id": "1001", "order_name": "#1001", "status": "picking"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "picking"}


async def test_set_status_unknown_is_400(staff_client, monkeypatch):
    monkeypatch.setattr(orders.shopify_orders, "set_state", lambda *a, **k: None)
    r = await staff_client.post("/api/orders/status", json={
        "order_id": "1001", "order_name": "#1001", "status": "не-съществува"})
    assert r.status_code == 400


async def test_set_status_requires_auth(client):
    r = await client.post("/api/orders/status", json={
        "order_id": "1", "order_name": "#1", "status": "picking"})
    assert r.status_code == 401
