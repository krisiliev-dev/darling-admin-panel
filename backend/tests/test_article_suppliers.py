# -*- coding: utf-8 -*-
"""#70 slice 5: article-supplier surface — read shape, full-replace write, one-default rule, gate."""
import pytest


@pytest.mark.asyncio
async def test_read_article_suppliers_shape(owner_client, barsy_recorder):
    barsy_recorder.set_return("articles_suppliers", {"rows": [
        {"supplier_id": 3, "supplier_name": "НАИС ООД", "remote_ref_id": "X1",
         "remote_article_name": "ЧИНИЯ", "is_default": 1, "default_delivery_price": "2.50", "package_id": ""},
        {"supplier_id": None, "supplier_name": "boilerplate row"}]})
    r = (await owner_client.get("/api/catalog/55/suppliers")).json()
    assert r["suppliers"] == [{"id": 3, "supplier_id": 3, "supplier": "НАИС ООД", "supplier_code": "X1",
                               "supplier_name": "ЧИНИЯ", "is_default": True, "delivery_price": "2.50",
                               "package_id": ""}]


@pytest.mark.asyncio
async def test_put_full_replace_resolves_new_and_one_default(owner_client, barsy_recorder):
    barsy_recorder.set_return("Suppliers_getlist", [
        {"supplier_id": 7, "supplier_name": "ВЕРТЕКС ООД"}, {"supplier_id": 9, "supplier_name": "МИРА-Н"}])
    r = (await owner_client.put("/api/catalog/55/suppliers", json={"rows": [
        {"supplier_id": 3, "supplier": "НАИС ООД", "supplier_code": "X1", "is_default": True},
        {"supplier": "Вертекс", "supplier_code": "V9", "is_default": True},
    ]})).json()
    assert r["ok"] is True and r["count"] == 2 and r["skipped"] == []
    body = barsy_recorder.body_for("Articles_save_suppliers")
    assert body["id"] == 55 and body["action_type"] == "save" and body["values"] == {}
    rows = body["rows"]
    assert [x["supplier_id"] for x in rows] == [3, 7]              # kept id + resolved-by-name
    assert [x["is_default"] for x in rows] == [1, 0]               # exactly one Основен
    assert rows[1]["remote_ref_id"] == "V9"


@pytest.mark.asyncio
async def test_put_nothing_resolvable_is_400_not_a_wipe(owner_client, barsy_recorder):
    barsy_recorder.set_return("Suppliers_getlist", [])
    r = await owner_client.put("/api/catalog/55/suppliers", json={"rows": [{"supplier": "Никой"}]})
    assert r.status_code == 400
    assert "Articles_save_suppliers" not in barsy_recorder.operations


@pytest.mark.asyncio
async def test_put_gated_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    from app.routers import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "barsy_write_live", lambda: False)
    r = (await owner_client.put("/api/catalog/55/suppliers", json={"rows": [
        {"supplier_id": 3, "supplier": "НАИС"}]})).json()
    assert r["gated"] is True
    assert "Articles_save_suppliers" not in barsy_recorder.operations


@pytest.mark.asyncio
async def test_article_suppliers_require_auth(client):
    assert (await client.get("/api/catalog/55/suppliers")).status_code == 401
