# -*- coding: utf-8 -*-
"""Доставки (/api/purchasing) — the READ surfaces + error paths not covered by
test_purchasing.py (which focuses on the Storeloads_create body + close).

Here: /articles (PO picker, code-vs-name filter), /reorder (below-min suggestions +
suggested_qty math), /orders (storeload list mapping), the 502 upstream path when Barsy
raises, and auth gating on the reads.
"""

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
async def test_list_orders_maps_only_what_the_list_actually_carries(staff_client, barsy_recorder):
    """MEASURED shape — Storeloads_getlist rows are exactly these six keys and no more
    (claude/BARSY_LIST_SHAPES_20260808.md).

    The previous version of this test fed supplier_id, supplier_name, doc_num and total as well,
    and asserted the supplier name came through. None of those keys exists in a real response,
    so it certified a mapping that has always produced empty strings. The endpoint no longer
    claims them; the supplier, number, date and total live on Storeloads_get."""
    barsy_recorder.set_return("Storeloads_getlist", [
        {"barsy_id": 1, "depot_id": 1, "doc_type_id": 1, "doc_type_name": "Доставка",
         "status": 0, "store_load_id": 1},
        {"barsy_id": 1, "depot_id": 1, "doc_type_id": 1, "doc_type_name": "Доставка",
         "status": 1, "store_load_id": 2},
    ])
    r = await staff_client.get("/api/purchasing/orders")
    assert r.status_code == 200, r.text
    orders = {o["id"]: o for o in r.json()["orders"]}
    assert orders[1]["status"] == "draft"    # status 0
    assert orders[2]["status"] == "closed"   # status 1
    assert orders[1]["doc_type"] == "Доставка"
    assert orders[1]["depot_id"] == 1
    # and nothing it cannot know: an empty string here would read as "a document with no
    # supplier" rather than as "a field this call does not fetch"
    assert "supplier" not in orders[1]
    assert "doc_num" not in orders[1]
    assert "doc_date" not in orders[1]
    assert "total" not in orders[1]




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
