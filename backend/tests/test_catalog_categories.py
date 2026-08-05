# -*- coding: utf-8 -*-
"""#70 slice 4: Barsy category picker endpoints + the live attribute-value pool."""
import pytest

from app.routers import catalog as catalog_module


@pytest.fixture(autouse=True)
def _fresh_caches():
    catalog_module._CAT_CACHE.update({"t": 0.0, "rows": []})
    catalog_module._ATTR_CACHE.update({"t": 0.0, "keys": {}, "values": {}})


@pytest.mark.asyncio
async def test_categories_search_words_anywhere(owner_client, barsy_recorder):
    barsy_recorder.set_return("Categories_getlist", [
        {"cat_id": -1, "cat_path": None, "cat_name": "Некатегоризирани"},        # dropped (no path)
        {"cat_id": 9044, "cat_path": "Дом и градина > Кухня > Чинии"},
        {"cat_id": 9050, "cat_path": "Дом и градина > Кухня > Тигани"},
        {"cat_id": 3828, "cat_path": "Животни > Живи животни"},
    ])
    r = (await owner_client.get("/api/catalog/categories?q=кухня чини")).json()
    assert [c["cat_id"] for c in r["categories"]] == [9044]        # every word must match
    r = (await owner_client.get("/api/catalog/categories")).json()
    assert r["total"] == 3                                          # -1 / pathless dropped
    assert barsy_recorder.operations.count("Categories_getlist") == 1   # served from the cache


@pytest.mark.asyncio
async def test_category_slug_resolved_on_pick(owner_client, barsy_recorder):
    barsy_recorder.set_return("Categories_get",
                              {"cat_id": 9044, "cat_path": "Дом и градина > Кухня > Чинии", "slug": "hg-11-3-13"})
    r = (await owner_client.get("/api/catalog/categories/9044")).json()
    assert r["slug"] == "hg-11-3-13" and r["cat_id"] == 9044
    barsy_recorder.set_return("Categories_get", {})
    assert (await owner_client.get("/api/catalog/categories/999")).status_code == 404


@pytest.mark.asyncio
async def test_attr_values_pool(owner_client, barsy_recorder):
    barsy_recorder.set_return("ArticleDetails_getlist", [
        {"key_id": 4, "detail_name": "Бранд"}, {"key_id": 7, "detail_name": "Цвят"}])
    barsy_recorder.set_return("ArticleDetails_getKeyValues", [
        {"detail_id": 6, "detail_value": "E-Cloth"}, {"detail_id": 9, "detail_value": "Wealucky"}])
    r = (await owner_client.get("/api/catalog/attr-values?key=Бранд")).json()
    assert r["values"] == ["E-Cloth", "Wealucky"]
    body = barsy_recorder.body_for("ArticleDetails_getKeyValues")
    assert body["key_id"] == 4                                      # name resolved to the key id
    r = (await owner_client.get("/api/catalog/attr-values?key=Няма такъв")).json()
    assert r["values"] == []                                        # schema-only attribute


@pytest.mark.asyncio
async def test_categories_require_auth(client):
    assert (await client.get("/api/catalog/categories")).status_code == 401
    assert (await client.get("/api/catalog/attr-values?key=Цвят")).status_code == 401
