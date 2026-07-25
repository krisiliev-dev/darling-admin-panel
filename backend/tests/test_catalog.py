# -*- coding: utf-8 -*-
"""Каталог (/api/catalog) — browse + edit products straight from Barsy.

Barsy is the system of record; we mock the shared barsy.call so nothing hits the real ERP.
The load-bearing contract under test is the Articles_update body mapping: name → article_name
+ article_name_ml, description → description_ml, barcode as a STRING (Barsy rejects the array
form), and price is NEVER written from here (price policy — Microinvest is the source).
"""
import pytest


# --- read paths: any authenticated user may browse ---------------------------------
async def test_list_articles_readable_by_staff(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "SKU-1", "article_name": "Чаша", "actual_price": "5.00"},
        {"article_id": 2, "code": "SKU-2", "article_name": "Чиния", "actual_price": "8.00"},
    ])
    r = await staff_client.get("/api/catalog")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["article_id"] for i in items] == [1, 2]
    # core fields + stock folded in (None when Barsy stock is empty)
    assert {k: items[0][k] for k in ("article_id", "code", "name", "price")} == \
        {"article_id": 1, "code": "SKU-1", "name": "Чаша", "price": "5.00"}
    assert items[0]["stock"] is None and items[0]["low"] is False
    assert barsy_recorder.operations == ["Articles_getlist", "Store_amounts"]  # stock join


async def test_catalog_folds_stock_and_low_filter(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "A", "article_name": "Чаша", "actual_price": "5.00"},
        {"article_id": 2, "code": "B", "article_name": "Чиния", "actual_price": "8.00"},
    ])
    barsy_recorder.set_return("Store_amounts", {"rows": [
        {"article_id": 1, "store_amount": "2", "min_amount": "5", "below_min": 1},   # low
        {"article_id": 2, "store_amount": "50", "min_amount": "5"},                   # ok
    ]})
    items = (await staff_client.get("/api/catalog")).json()["items"]
    by_id = {i["article_id"]: i for i in items}
    assert by_id[1]["stock"] == 2 and by_id[1]["low"] is True
    assert by_id[2]["stock"] == 50 and by_id[2]["low"] is False
    # low_only filter keeps just the low one
    low = (await staff_client.get("/api/catalog?low_only=true")).json()["items"]
    assert [i["article_id"] for i in low] == [1]


async def test_list_articles_readable_by_packer(packer_client, barsy_recorder):
    # browse is gated by current_active_user only — even the lowest role may read
    barsy_recorder.set_return("Articles_getlist", [])
    r = await packer_client.get("/api/catalog")
    assert r.status_code == 200


async def test_get_article_collapses_barcode_list(owner_client, barsy_recorder):
    # Barsy may return barcode as a list; the detail view collapses it to the first value
    barsy_recorder.set_return("Articles_get", {
        "article_id": 55, "code": "SKU-55", "article_name": "Кана",
        "description": "описание", "actual_price": "12.50",
        "barcode": ["3800130123456", "3800130999999"],
    })
    r = await owner_client.get("/api/catalog/55")
    assert r.status_code == 200, r.text
    assert r.json()["barcode"] == "3800130123456"


async def test_get_missing_article_is_404(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {})  # no article_id → not found
    r = await owner_client.get("/api/catalog/999")
    assert r.status_code == 404


async def test_list_articles_requires_auth(client):
    r = await client.get("/api/catalog")
    assert r.status_code == 401


# --- write path: Articles_update body mapping --------------------------------------
async def test_update_body_maps_fields_and_omits_price(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/55", json={
        "name": "  Нова кана  ",
        "description": "Голяма керамична кана",
        "barcode": "3800130123456",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    body = barsy_recorder.body_for("Articles_update")
    # article_id lives at the TOP level of the update body
    assert body["article_id"] == 55
    art = body["article"]
    # name → article_name (+ trimmed) and multilingual bg_BG
    assert art["article_name"] == "Нова кана"
    assert art["article_name_ml"] == {"bg_BG": "Нова кана"}
    # description → description_ml
    assert art["description_ml"] == {"bg_BG": "Голяма керамична кана"}
    # barcode is a plain STRING, never an array
    assert art["barcode"] == "3800130123456"
    assert isinstance(art["barcode"], str)
    # price is read-only here — the update must NOT carry any price key
    assert "actual_price" not in art
    assert "price" not in art


async def test_update_omits_optional_fields_when_blank(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Само име"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["article_name"] == "Само име"
    assert "description_ml" not in art
    assert "barcode" not in art


async def test_update_empty_name_is_400(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "   "})
    assert r.status_code == 400
    assert barsy_recorder.calls == []  # never reached the ERP


async def test_update_refuses_non_staff(packer_client, barsy_recorder):
    # packer is below 'staff' and is not a superuser → require_roles('staff') refuses
    r = await packer_client.post("/api/catalog/7", json={"name": "Опит"})
    assert r.status_code == 403
    assert barsy_recorder.calls == []


async def test_update_allows_owner_superuser(owner_client, barsy_recorder):
    # a superuser (owner/manager) always passes require_roles
    r = await owner_client.post("/api/catalog/7", json={"name": "От собственик"})
    assert r.status_code == 200, r.text
    assert barsy_recorder.operations == ["Articles_update"]


async def test_update_requires_auth(client, barsy_recorder):
    r = await client.post("/api/catalog/7", json={"name": "Х"})
    assert r.status_code == 401
    assert barsy_recorder.calls == []
