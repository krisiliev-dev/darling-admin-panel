# -*- coding: utf-8 -*-
"""Начало (/api/summary/home) — quick-glance KPIs + orders-to-pack. Sales come from Shopify
orders (mocked here); profit uses a local cost map; op_status decides done vs to-pack."""
from datetime import datetime, timezone

import app.routers.home as home


async def test_home_empty_prelaunch(owner_client, monkeypatch):
    monkeypatch.setattr(home.shopify_orders, "fetch_orders", lambda limit: [])
    monkeypatch.setattr(home.shopify_orders, "state_map", lambda: {})
    monkeypatch.setattr(home, "_reorder_count", lambda: 0)
    r = await owner_client.get("/api/summary/home?period=today")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["has_sales"] is False
    assert b["kpi"] == {"turnover": 0, "profit": 0, "profit_complete": True, "units": 0,
                        "orders_done": 0, "orders_to_pack": 0, "reorder_n": 0, "top_product": None}
    assert b["to_pack"] == [] and b["attention"] == []


async def test_home_computes_kpis_and_cards(owner_client, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    orders = [
        {"id": "o1", "name": "#1", "created": now, "total": 30.0, "customer": "Иван",
         "city": "София", "items": [{"title": "Чаша", "qty": 2, "sku": "C1"}]},
        {"id": "o2", "name": "#2", "created": now, "total": 10.0, "customer": "",
         "city": "", "items": [{"title": "Чиния", "qty": 1, "sku": "C2"}]},
    ]
    monkeypatch.setattr(home.shopify_orders, "fetch_orders", lambda limit: [dict(o) for o in orders])
    monkeypatch.setattr(home.shopify_orders, "state_map",
                        lambda: {"o1": {"status": "shipped"}, "o2": {"status": "new"}})
    monkeypatch.setattr(home, "_reorder_count", lambda: 3)
    monkeypatch.setattr(home, "_cost_by_code", lambda: {"C1": 5.0})  # cost known only for C1

    r = await owner_client.get("/api/summary/home?period=today")
    assert r.status_code == 200, r.text
    k = r.json()["kpi"]
    assert k["turnover"] == 40.0
    assert k["units"] == 3
    assert k["profit"] == 40.0 - 2 * 5.0            # COGS only where cost is known
    assert k["profit_complete"] is False            # C2 has no cost → profit flagged as estimate
    assert k["orders_to_pack"] == 1 and k["orders_done"] == 1   # o2 new→to-pack, o1 shipped→done
    assert k["top_product"] == {"name": "Чаша", "units": 2}
    assert k["reorder_n"] == 3
    body = r.json()
    assert [c["id"] for c in body["to_pack"]] == ["o2"]          # only the pending order is a card
    assert any("дозареждане" in a["text"] for a in body["attention"])


async def test_home_period_window_excludes_old(owner_client, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    orders = [
        {"id": "new", "name": "#n", "created": now, "total": 5.0, "items": [{"title": "A", "qty": 1, "sku": "A"}]},
        {"id": "old", "name": "#o", "created": "2020-01-01T00:00:00+00:00", "total": 99.0,
         "items": [{"title": "B", "qty": 9, "sku": "B"}]},
    ]
    monkeypatch.setattr(home.shopify_orders, "fetch_orders", lambda limit: [dict(o) for o in orders])
    monkeypatch.setattr(home.shopify_orders, "state_map", lambda: {})
    monkeypatch.setattr(home, "_reorder_count", lambda: 0)
    monkeypatch.setattr(home, "_cost_by_code", lambda: {})
    r = await owner_client.get("/api/summary/home?period=today")
    assert r.json()["kpi"]["turnover"] == 5.0   # the 2020 order is outside the window


async def test_home_requires_auth(client):
    r = await client.get("/api/summary/home")
    assert r.status_code == 401
