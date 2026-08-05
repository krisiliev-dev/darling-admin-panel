# -*- coding: utf-8 -*-
"""Справки proxy (#84): whitelist listing, the structure+values proxy, column tightening, auth."""
import pytest

from app.routers import reports as reports_module


@pytest.fixture(autouse=True)
def _fresh_cache():
    reports_module._struct_cache.clear()


def _mock_report(barsy_recorder, rows):
    STRUCT = {"content": [{"type": "eStructList", "id": "eStructList_1", "data": {"elements": [
        {"name": "article_name", "title": "Артикул"},
        {"name": "actual_price", "title": "Цена"},
        {"name": "detail_9", "title": "Бранд"},
    ]}}]}
    VALUES = {"rows": rows, "total": len(rows)}
    barsy_recorder.set_return("reports_articles_actual_prices",
                              lambda b: STRUCT if "params" in (b or {}) else VALUES)


@pytest.mark.asyncio
async def test_list_reports_grouped(owner_client):
    r = (await owner_client.get("/api/reports")).json()
    groups = {g["group"]: [x["key"] for x in g["reports"]] for g in r["groups"]}
    assert "sell_price_changes" in groups["Артикули"]
    assert "sales_by_articles" in groups["Продажби"]


@pytest.mark.asyncio
async def test_report_proxy_columns_and_rows(owner_client, barsy_recorder):
    _mock_report(barsy_recorder, [{"article_name": "Ваза", "actual_price": 9.99, "detail_9": None}])
    r = (await owner_client.get("/api/reports/sell_price_changes")).json()
    assert r["total"] == 1 and r["rows"][0]["article_name"] == "Ваза"
    # detail_9 is empty in the data -> dropped from columns; titles come from the page structure
    assert [c["name"] for c in r["columns"]] == ["article_name", "actual_price"]
    assert r["columns"][0]["title"] == "Артикул"
    body = barsy_recorder.body_for("reports_articles_actual_prices")
    assert body == {"params": {"bid": 1}}                       # structure first
    values_calls = [b for op, b in barsy_recorder.calls
                    if op == "reports_articles_actual_prices" and "action_type" in (b or {})]
    assert values_calls and values_calls[0]["rows"] == 100 and values_calls[0]["page_num"] == 1


@pytest.mark.asyncio
async def test_report_structure_cached_between_reads(owner_client, barsy_recorder):
    _mock_report(barsy_recorder, [])
    await owner_client.get("/api/reports/sell_price_changes")
    await owner_client.get("/api/reports/sell_price_changes?page=2")
    struct_calls = [b for op, b in barsy_recorder.calls
                    if op == "reports_articles_actual_prices" and "params" in (b or {})]
    assert len(struct_calls) == 1                               # one structure read, cached after


@pytest.mark.asyncio
async def test_unknown_report_is_404(owner_client):
    assert (await owner_client.get("/api/reports/nope")).status_code == 404


@pytest.mark.asyncio
async def test_reports_require_auth(client):
    # NB: owner_client logs the SHARED client in, so this must be its own test
    assert (await client.get("/api/reports")).status_code == 401
