# -*- coding: utf-8 -*-
"""Доставки (/api/purchasing) — the READ surfaces + error paths not covered by
test_purchasing.py (which focuses on the Storeloads_create body + close).

Here: /articles (PO picker, code-vs-name filter), /reorder (below-min suggestions +
suggested_qty math), /orders (storeload list mapping), the 502 upstream path when Barsy
raises, and auth gating on the reads.
"""
import pytest

import app.routers.purchasing as purchasing


# --- GET /articles -----------------------------------------------------------------
async def test_articles_numeric_query_filters_by_code(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "12345", "article_name": "Чаша", "actual_price": "5.00"}])
    r = await staff_client.get("/api/purchasing/articles", params={"q": "12345"})
    assert r.status_code == 200, r.text
    assert r.json()["articles"][0]["code"] == "12345"
    # a digits-only query is sent as a `code` filter, not article_name
    assert barsy_recorder.body_for("Articles_getlist")["filters"] == {"code": "12345"}


async def test_articles_text_query_filters_by_name(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [])
    await staff_client.get("/api/purchasing/articles", params={"q": "чаша"})
    assert barsy_recorder.body_for("Articles_getlist")["filters"] == {"article_name": "чаша"}


async def test_articles_rejects_bad_limit(staff_client, barsy_recorder):
    r = await staff_client.get("/api/purchasing/articles", params={"limit": "999"})
    assert r.status_code == 422


# --- GET /reorder ------------------------------------------------------------------
async def test_reorder_computes_suggested_qty(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", {"rows": [
        {"article_id": 1, "article_name": "Чаша", "store_amount": "2", "min_amount": "5",
         "amount_type_name_short": "бр"},
        {"article_id": 2, "article_name": "Лошо", "store_amount": "abc", "min_amount": "x"},
    ]})
    r = await staff_client.get("/api/purchasing/reorder")
    assert r.status_code == 200, r.text
    body = r.json()
    # the unparseable row is skipped; one valid suggestion remains
    assert body["count"] == 1
    item = body["items"][0]
    # suggested_qty = max(min*2 - amount, min) = max(10-2, 5) = 8
    assert item["suggested_qty"] == 8
    assert item["amount"] == 2.0 and item["min_amount"] == 5.0


async def test_reorder_empty_when_no_low_stock(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", {"rows": []})
    r = await staff_client.get("/api/purchasing/reorder")
    assert r.json() == {"count": 0, "items": []}


# --- GET /orders (storeload list) --------------------------------------------------
async def test_list_orders_maps_status(staff_client, barsy_recorder):
    barsy_recorder.set_return("Storeloads_getlist", [
        {"store_load_id": 1, "supplier_id": 5, "supplier_name": "Алфа", "status": 0,
         "doc_num": "D-1", "total": 100},
        {"store_load_id": 2, "supplier_id": 6, "supplier_name": "Бета", "status": 1,
         "doc_num": "D-2", "total": 200},
    ])
    r = await staff_client.get("/api/purchasing/orders")
    assert r.status_code == 200, r.text
    orders = {o["id"]: o for o in r.json()["orders"]}
    assert orders[1]["status"] == "draft"   # status 0
    assert orders[2]["status"] == "closed"  # status 1
    assert orders[1]["supplier"] == "Алфа"


# --- error + auth paths ------------------------------------------------------------
async def test_suppliers_barsy_failure_is_502(staff_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy timeout")
    monkeypatch.setattr(purchasing.barsy, "call", boom)
    r = await staff_client.get("/api/purchasing/suppliers")
    assert r.status_code == 502
    assert "Barsy доставчици" in r.json()["detail"]


async def test_reorder_requires_auth(client):
    r = await client.get("/api/purchasing/reorder")
    assert r.status_code == 401


async def test_articles_requires_auth(client):
    r = await client.get("/api/purchasing/articles")
    assert r.status_code == 401
