# -*- coding: utf-8 -*-
"""Доставки (/api/purchasing) — supplier orders as Barsy draft storeloads.

Barsy is the system of record; nothing order-related is stored locally. We mock the shared
barsy.call, so the assertion target is the Storeloads_create body: action_type toggles
save / save_and_close, the header carries depot_id + supplier_id, and each article row carries
article_id / amount / current_price.
"""

import app.routers.purchasing as purchasing

_LINES = [
    {"article_id": 101, "name": "Чаша", "amount": 12, "current_price": 3.40},
    {"article_id": 102, "name": "Чиния", "amount": 6, "current_price": 5.10},
]


def _prime_depot(barsy_recorder, depot_id=3):
    """_default_depot is lru_cached; clear it and feed a known Depots_getlist so the storeload
    header's depot_id is deterministic within this test."""
    purchasing._default_depot.cache_clear()
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": depot_id}])


async def test_create_order_draft_uses_save(staff_client, barsy_recorder):
    _prime_depot(barsy_recorder)
    barsy_recorder.set_return("Storeloads_create", 7001)
    r = await staff_client.post("/api/purchasing/orders", json={
        "supplier_id": 55, "note": "юли", "lines": _LINES, "close": False})
    assert r.status_code == 201, r.text
    assert r.json() == {"ok": True, "storeload_id": 7001, "status": "draft"}

    body = barsy_recorder.body_for("Storeloads_create")
    assert body["action_type"] == "save"        # draft (заявка), not received into stock
    header = body["store_load"]
    assert header["supplier_id"] == 55
    assert header["depot_id"] == 3
    rows = body["articles"]
    assert [row["article_id"] for row in rows] == [101, 102]
    assert rows[0]["amount"] == 12
    assert rows[0]["current_price"] == 3.40
    # every row carries the three load-bearing fields
    for row in rows:
        assert {"article_id", "amount", "current_price"} <= set(row)


async def test_create_order_close_uses_save_and_close(staff_client, barsy_recorder):
    _prime_depot(barsy_recorder)
    barsy_recorder.set_return("Storeloads_create", 7002)
    r = await staff_client.post("/api/purchasing/orders", json={
        "supplier_id": 55, "lines": _LINES, "close": True})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "closed"
    assert barsy_recorder.body_for("Storeloads_create")["action_type"] == "save_and_close"


async def test_create_order_empty_lines_is_400(staff_client, barsy_recorder):
    r = await staff_client.post("/api/purchasing/orders", json={
        "supplier_id": 55, "lines": [], "close": False})
    assert r.status_code == 400
    assert barsy_recorder.calls == []  # rejected before any ERP contact


async def test_create_order_refuses_non_staff(packer_client, barsy_recorder):
    r = await packer_client.post("/api/purchasing/orders", json={
        "supplier_id": 55, "lines": _LINES, "close": False})
    assert r.status_code == 403
    assert barsy_recorder.calls == []


async def test_create_order_requires_auth(client, barsy_recorder):
    r = await client.post("/api/purchasing/orders", json={
        "supplier_id": 55, "lines": _LINES, "close": False})
    assert r.status_code == 401
    assert barsy_recorder.calls == []


# --- read paths (any authed user) + close ------------------------------------------
async def test_suppliers_readable_and_filtered(staff_client, barsy_recorder):
    barsy_recorder.set_return("Suppliers_getlist", [
        {"supplier_id": 2, "supplier_name": "Бета", "delete_flag": 0},
        {"supplier_id": 9, "supplier_name": "Изтрит", "delete_flag": 1},
        {"supplier_id": 1, "supplier_name": "Алфа"},
    ])
    r = await staff_client.get("/api/purchasing/suppliers")
    assert r.status_code == 200, r.text
    suppliers = r.json()["suppliers"]
    # deleted supplier dropped, remainder sorted by name
    assert [s["supplier_name"] for s in suppliers] == ["Алфа", "Бета"]


async def test_close_order_calls_storeloads_close(staff_client, barsy_recorder):
    r = await staff_client.post("/api/purchasing/orders/7001/close")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "closed"}
    assert barsy_recorder.body_for("Storeloads_close") == {"id": 7001}
