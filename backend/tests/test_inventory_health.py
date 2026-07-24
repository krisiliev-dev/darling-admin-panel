# -*- coding: utf-8 -*-
"""Здраве на инвентара — lot-ageing buckets + stale-stock alert.

Pure aggregation (app.inventory_health.aging_report) is tested with a fixed `today` so bucket
boundaries and frozen-capital sums are deterministic. The endpoint is tested with a mocked
Store_amounts_lots so we assert the fetch→normalize→aggregate wiring without a live Barsy.
"""
from datetime import date

import pytest

import app.inventory_health as ih
import app.routers.inventory_health as router


TODAY = date(2026, 7, 22)


def _lot(aid, name, qty, cost, received, supplier="", sell=None):
    return {"article_id": aid, "name": name, "qty": qty, "unit_cost": cost,
            "supplier": supplier, "sell_price": sell, "received": received}


def test_bucketing_boundaries_are_half_open_upward():
    # exactly 90 days → 90–180 bucket (lo inclusive, hi exclusive); 89 → 0–90
    lots = [_lot(1, "A", 1, 10, date(2026, 4, 23)),   # 90 days → 90-180
            _lot(2, "B", 1, 10, date(2026, 4, 24))]   # 89 days → 0-90
    r = ih.aging_report(lots, today=TODAY)
    by = {b["key"]: b for b in r["buckets"]}
    assert by["0-90"]["lots"] == 1
    assert by["90-180"]["lots"] == 1


def test_capital_at_cost_and_stale_alert():
    lots = [
        _lot(10, "Стар сервиз", 5, 20.0, date(2025, 1, 1), "Алфа", sell=39.0),   # ~567d, 365+, €100
        _lot(10, "Стар сервиз", 3, 20.0, date(2026, 6, 1), "Алфа", sell=39.0),   # ~51d, 0-90, €60
        _lot(20, "Ваза", 2, 15.0, date(2025, 12, 1), "Бета"),                     # ~233d, 180-365, €30
        _lot(30, "Нова кутия", 10, 5.0, date(2026, 7, 1), "Гама"),                # ~21d, 0-90, €50
    ]
    r = ih.aging_report(lots, today=TODAY, stale_days=180)
    # totals across all on-hand
    assert r["totals"]["value"] == pytest.approx(240.0)
    assert r["totals"]["articles"] == 3
    # stale = lots >= 180d: art10 oldest lot (€100) + art20 (€30) = €130
    st = r["stale"]
    assert st["value"] == pytest.approx(130.0)
    assert st["articles"] == 2
    assert st["pct_of_value"] == pytest.approx(round(130.0 / 240.0 * 100, 1))
    # worst-first by frozen capital
    assert st["items"][0]["article_id"] == 10
    assert st["items"][0]["oldest_days"] >= 365
    # only the stale lot's value is counted for art10, not its fresh €60 lot
    assert st["items"][0]["value"] == pytest.approx(100.0)


def test_markdown_ladder_by_age():
    assert ih.markdown_for(30) == 0
    assert ih.markdown_for(120) == 15
    assert ih.markdown_for(200) == 30
    assert ih.markdown_for(400) == 50


def test_stale_items_carry_markdown_and_new_price():
    lots = [
        _lot(10, "Много стар", 5, 20.0, date(2025, 1, 1), sell=40.0),   # ~567d → 365+ → 50%
        _lot(20, "Стар", 2, 15.0, date(2025, 12, 1)),                    # ~233d → 180-365 → 30%, no sell
    ]
    r = ih.aging_report(lots, today=TODAY, stale_days=180)
    by = {x["article_id"]: x for x in r["stale"]["items"]}
    assert by[10]["suggested_markdown_pct"] == 50
    assert by[10]["suggested_price"] == pytest.approx(20.0)   # 40 × (1-0.50)
    assert by[20]["suggested_markdown_pct"] == 30
    assert by[20]["suggested_price"] is None                  # no sell price known → % only


def test_stale_threshold_is_configurable():
    lots = [_lot(1, "A", 1, 10, date(2026, 1, 1))]  # ~202 days
    assert ih.aging_report(lots, today=TODAY, stale_days=180)["stale"]["articles"] == 1
    assert ih.aging_report(lots, today=TODAY, stale_days=365)["stale"]["articles"] == 0


def test_undated_lots_are_counted_not_dropped():
    lots = [_lot(1, "A", 4, 10, None)]  # no receipt date
    r = ih.aging_report(lots, today=TODAY)
    assert r["undated"]["lots"] == 1
    assert r["undated"]["value"] == pytest.approx(40.0)
    assert r["totals"]["value"] == pytest.approx(40.0)   # still in totals
    assert sum(b["lots"] for b in r["buckets"]) == 0     # but in no age bucket


def test_zero_and_negative_qty_ignored():
    lots = [_lot(1, "A", 0, 10, TODAY), _lot(2, "B", -3, 10, TODAY), _lot(3, "C", 2, 10, TODAY)]
    r = ih.aging_report(lots, today=TODAY)
    assert r["totals"]["lots"] == 1
    assert r["totals"]["units"] == pytest.approx(2.0)


def test_parse_date_formats():
    assert ih.parse_date("2026-07-22") == date(2026, 7, 22)
    assert ih.parse_date("2026-07-22 13:45:01") == date(2026, 7, 22)
    assert ih.parse_date("22.07.2026") == date(2026, 7, 22)
    assert ih.parse_date("") is None
    assert ih.parse_date(None) is None


def test_normalize_picks_first_present_candidate():
    row = {"article_id": 7, "article_name": "Тест", "lot_amount": "4",
           "avg_delivery_price": "12.5", "load_date": "2025-01-01", "current_price": "25"}
    n = router._normalize(row)
    assert n["article_id"] == 7 and n["name"] == "Тест"
    assert n["qty"] == "4" and n["unit_cost"] == "12.5"
    assert n["received"] == "2025-01-01" and n["sell_price"] == "25"


# ---- endpoint wiring ----

async def test_aging_endpoint_wires_barsy(owner_client, monkeypatch):
    page = {"total": 2, "rows": [
        {"article_id": 1, "article_name": "Стар", "lot_amount": "5", "avg_delivery_price": "20",
         "load_date": "2025-01-01", "partner_name": "Алфа", "current_price": "39"},
        {"article_id": 2, "article_name": "Нов", "lot_amount": "3", "avg_delivery_price": "5",
         "load_date": "2026-07-01"},
    ]}
    monkeypatch.setattr(router.barsy, "call", lambda method, body: page)
    r = await owner_client.get("/api/inventory-health/aging?stale_days=180")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "barsy:Store_amounts_lots"
    assert body["totals"]["articles"] == 2
    assert body["stale"]["articles"] == 1          # only the 2025 lot is stale
    assert body["stale"]["items"][0]["name"] == "Стар"


async def test_aging_requires_auth(client):
    r = await client.get("/api/inventory-health/aging")
    assert r.status_code == 401


# ---- historical diagnosis (seeded) ----

async def test_diagnosis_serves_seed_when_present(owner_client):
    r = await owner_client.get("/api/inventory-health/diagnosis")
    assert r.status_code == 200, r.text
    body = r.json()
    # seed ships with the app; if present it must expose the diagnosis shape in EUR
    if body["available"]:
        assert body["meta"]["currency"] == "EUR"
        assert "turnover" in body["kpi"] and "dsi" in body["kpi"] and "dead_val" in body["kpi"]
        assert isinstance(body.get("ts"), dict) and isinstance(body.get("suppliers"), list)


async def test_diagnosis_available_flag_when_seed_missing(owner_client, monkeypatch):
    monkeypatch.setattr(router, "_SEED_PATH", "/no/such/seed.json")
    router._seed.cache_clear()
    try:
        r = await owner_client.get("/api/inventory-health/diagnosis")
        assert r.status_code == 200
        assert r.json() == {"available": False}
    finally:
        router._seed.cache_clear()  # don't leak the bad path to other tests


async def test_diagnosis_requires_auth(client):
    r = await client.get("/api/inventory-health/diagnosis")
    assert r.status_code == 401


# ---- go-forward live metrics ----

import app.inventory_metrics as im


def test_metrics_velocity_cover_reorder_dead():
    sales = [
        {"article_id": 1, "name": "Бърз", "units": 365, "revenue": 3650, "cogs": 1825},  # 1/day
        # art2 has no sales row → dead if stocked
    ]
    stock = [
        {"article_id": 1, "name": "Бърз", "stock": 10, "unit_cost": 5, "sell_price": 10},
        {"article_id": 2, "name": "Застоял", "stock": 4, "unit_cost": 5, "sell_price": 12},
    ]
    r = im.compute(sales, stock, window_days=365, lead_days=14, safety_days=7, today=TODAY)
    a1 = next(x for x in r["reorder"] if x["article_id"] == 1)
    assert a1["velocity"] == pytest.approx(1.0)
    assert a1["days_of_cover"] == pytest.approx(10.0)     # 10 units ÷ 1/day
    assert a1["needs_reorder"] is True                     # 10 ≤ 1×(14+7)=21
    # suggested qty tops up to (lead14+safety7+coverage30)=51 days ÷ 1/day − 10 on hand = 41
    assert a1["suggested_qty"] == 41
    # art2 = dead (stock, zero sales)
    assert r["kpi"]["dead_n"] == 1
    assert r["kpi"]["dead_value"] == pytest.approx(20.0)
    assert r["dead"][0]["article_id"] == 2


def test_metrics_portfolio_turnover_dsi_gmroi():
    # COGS 1825 over 365d = annual 1825; inv value = 10×5 + 4×5 = 70 → turnover ≈ 26.07
    sales = [{"article_id": 1, "units": 365, "revenue": 3650, "cogs": 1825}]
    stock = [{"article_id": 1, "stock": 10, "unit_cost": 5},
             {"article_id": 2, "stock": 4, "unit_cost": 5}]
    k = im.compute(sales, stock, window_days=365, today=TODAY)["kpi"]
    assert k["inv_value"] == pytest.approx(70.0)
    assert k["turnover"] == pytest.approx(26.07, abs=0.05)
    assert k["dsi"] == pytest.approx(14, abs=1)            # 365 ÷ 26.07
    # GMROI = (rev - cogs) / inv = (3650-1825)/70
    assert k["gmroi"] == pytest.approx((3650 - 1825) / 70, abs=0.05)


def test_metrics_empty_inputs_are_safe():
    r = im.compute([], [], today=TODAY)
    assert r["has_sales"] is False
    assert r["kpi"]["turnover"] is None and r["kpi"]["inv_value"] == 0.0
    assert r["reorder"] == [] and r["dead"] == [] and r["top_sellers"] == []


def test_metrics_window_annualises():
    # same 100 units over 30 days should annualise COGS to ~12× the monthly figure
    sales = [{"article_id": 1, "units": 100, "revenue": 1000, "cogs": 500}]
    stock = [{"article_id": 1, "stock": 50, "unit_cost": 10}]  # inv 500
    k = im.compute(sales, stock, window_days=30, today=TODAY)["kpi"]
    # annual cogs = 500 × 365/30 = 6083; turnover = 6083/500 ≈ 12.17
    assert k["turnover"] == pytest.approx(12.17, abs=0.1)


async def test_metrics_endpoint_stock_only_prelaunch(owner_client, monkeypatch):
    # Barsy has stock but no sales yet (sales report errors/empty) → valid stock-only report
    def fake_call(method, body):
        if method == "Store_amounts":
            if body.get("page_num", 1) > 1:
                return {"rows": []}
            return {"rows": [{"article_id": 1, "article_name": "Тест", "store_amount": "5",
                              "avg_delivery_price": "4", "current_price": "9"}]}
        raise RuntimeError("no sales history yet")  # Reports_sales_by_articles
    monkeypatch.setattr(router.barsy, "call", fake_call)
    r = await owner_client.get("/api/inventory-health/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_sales"] is False
    assert body["kpi"]["skus"] == 1
    assert body["kpi"]["dead_n"] == 1        # stocked, zero sales → dead


async def test_metrics_requires_auth(client):
    r = await client.get("/api/inventory-health/metrics")
    assert r.status_code == 401
