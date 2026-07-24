# -*- coding: utf-8 -*-
"""Каталог (/api/catalog) — the upstream error path + list-mapping edge not covered by
test_catalog.py (which covers browse/get/update body-mapping + role gating).

Here: Barsy failure on browse/detail/update -> 502, the query-limit validation, and the
list handler's client-side truncation to `limit`.
"""
import pytest

import app.routers.catalog as catalog


async def test_list_articles_barsy_failure_is_502(staff_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(catalog.barsy, "call", boom)
    r = await staff_client.get("/api/catalog")
    assert r.status_code == 502
    assert "Barsy каталог" in r.json()["detail"]


async def test_get_article_barsy_failure_is_502(staff_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(catalog.barsy, "call", boom)
    r = await staff_client.get("/api/catalog/5")
    assert r.status_code == 502


async def test_update_barsy_failure_is_502(staff_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(catalog.barsy, "call", boom)
    r = await staff_client.post("/api/catalog/5", json={"name": "Ново"})
    assert r.status_code == 502
    assert "Barsy обновяване" in r.json()["detail"]


async def test_list_articles_truncated_to_limit(staff_client, barsy_recorder):
    # Barsy returns more rows than requested; the handler truncates client-side to `limit`
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": i, "code": f"C{i}", "article_name": f"A{i}", "actual_price": "1"}
        for i in range(5)])
    r = await staff_client.get("/api/catalog", params={"limit": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 2


async def test_list_articles_query_sent_as_code_when_numeric(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [])
    await staff_client.get("/api/catalog", params={"q": "9988"})
    assert barsy_recorder.body_for("Articles_getlist")["filters"] == {"code": "9988"}


@pytest.mark.parametrize("limit", ["0", "101"])
async def test_list_articles_rejects_bad_limit(staff_client, barsy_recorder, limit):
    r = await staff_client.get("/api/catalog", params={"limit": limit})
    assert r.status_code == 422
