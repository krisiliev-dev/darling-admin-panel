# -*- coding: utf-8 -*-
"""Наличности (/api/inventory) — live stock from Barsy (Store_amounts) with low/zero
detection. Barsy is the source; we mock the shared barsy.call so nothing hits the real ERP.

Coverage: happy-path mapping + low/zero counting, the name filter (client-side), the
below_min / at-minimum low logic, query-param validation, auth gating, and the upstream
502 path when Barsy raises.
"""
import pytest

import app.routers.inventory as inventory


_ROWS = {
    "total": 3,
    "rows": [
        # healthy stock
        {"article_id": 1, "article_name": "Чаша", "store_amount": "10",
         "min_amount": "3", "amount_type_name_short": "бр", "avg_delivery_price": "2.5"},
        # at/under the explicit minimum -> low (not zero)
        {"article_id": 2, "article_name": "Чиния", "store_amount": "2",
         "min_amount": "5", "amount_type_name_short": "бр"},
        # zero on hand -> low + zero
        {"article_id": 3, "article_name": "Тенджера", "store_amount": "0",
         "min_amount": "0", "amount_type_name_short": "бр"},
    ],
}


async def test_inventory_maps_rows_and_counts_low_zero(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", _ROWS)
    r = await staff_client.get("/api/inventory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    # Чиния (<=min) and Тенджера (0) are low; Тенджера alone is zero
    assert body["summary"] == {"tracked": 3, "low": 2, "zero": 1}
    items = {i["article_id"]: i for i in body["items"]}
    assert items[1]["amount"] == 10.0 and items[1]["low"] is False
    assert items[2]["low"] is True and items[2]["zero"] is False
    assert items[3]["low"] is True and items[3]["zero"] is True
    assert items[1]["avg_cost"] == 2.5
    # request body carried the pagination shape Barsy expects
    sent = barsy_recorder.body_for("Store_amounts")
    assert sent["action_type"] == "values" and sent["page_num"] == 1


async def test_below_min_flag_marks_low(staff_client, barsy_recorder):
    # Barsy's own below_min flag marks low even when amount > min_amount is unknown
    barsy_recorder.set_return("Store_amounts", {"total": 1, "rows": [
        {"article_id": 9, "article_name": "Х", "store_amount": "7", "below_min": 1}]})
    r = await staff_client.get("/api/inventory")
    body = r.json()
    assert body["summary"]["low"] == 1
    assert body["items"][0]["low"] is True


async def test_inventory_name_filter_is_applied(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", _ROWS)
    r = await staff_client.get("/api/inventory", params={"q": "чаша"})
    body = r.json()
    # only Чаша survives the (case-insensitive) name filter...
    assert [i["article_id"] for i in body["items"]] == [1]
    # ...but the summary counts are computed over the FULL result set (pre-filter)
    assert body["summary"]["tracked"] == 3


async def test_low_only_sets_barsy_filter(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", {"total": 0, "rows": []})
    r = await staff_client.get("/api/inventory", params={"low_only": "true"})
    assert r.status_code == 200
    assert barsy_recorder.body_for("Store_amounts")["filters"] == {"below_min": 1}


async def test_empty_barsy_response_is_empty_state(staff_client, barsy_recorder):
    barsy_recorder.set_return("Store_amounts", None)   # Barsy nearly empty today
    r = await staff_client.get("/api/inventory")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "summary": {"tracked": 0, "low": 0, "zero": 0}, "items": []}


@pytest.mark.parametrize("params", [{"page": "0"}, {"rows": "0"}, {"rows": "999"}])
async def test_inventory_rejects_bad_pagination(staff_client, barsy_recorder, params):
    barsy_recorder.set_return("Store_amounts", {"rows": []})
    r = await staff_client.get("/api/inventory", params=params)
    assert r.status_code == 422


async def test_inventory_requires_auth(client, barsy_recorder):
    r = await client.get("/api/inventory")
    assert r.status_code == 401
    assert barsy_recorder.calls == []


async def test_inventory_barsy_failure_is_502(staff_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(inventory.barsy, "call", boom)
    r = await staff_client.get("/api/inventory")
    assert r.status_code == 502
    assert "Barsy наличности" in r.json()["detail"]
