# -*- coding: utf-8 -*-
"""Local product cache: sync from Barsy → instant filtered search (no per-keystroke Barsy call)."""
import pytest

SUP = "АНИС"
EXIST_CODE = "14153"   # in supplier_codes.jsonl under АНИС → supplier gets enriched on sync


async def _sync(owner_client, barsy_recorder, articles, stock_rows=None):
    barsy_recorder.set_return("Articles_getlist", articles)
    barsy_recorder.set_return("Store_amounts", {"rows": stock_rows or []})
    r = await owner_client.post("/api/catalog/sync")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_sync_then_search_filters(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": EXIST_CODE, "article_name": "Тиган 24см", "actual_price": "19.90", "picture_url": "/x.png"},
        {"article_id": 2, "code": "OTHER-9", "article_name": "Чиния бяла", "actual_price": "4.50"},
        {"article_id": 3, "code": "OTHER-8", "article_name": "Тиган 28см", "actual_price": "29.90"},
    ], stock_rows=[{"article_id": 2, "store_amount": "1", "min_amount": "5", "below_min": 1}])

    # status
    st = (await owner_client.get("/api/catalog/cache-status")).json()
    assert st["count"] == 3 and st["last_sync"]

    # name search (instant, over the cache)
    tigani = (await owner_client.get("/api/catalog/search?q=тиган")).json()
    assert tigani["total"] == 2
    assert {i["article_id"] for i in tigani["items"]} == {1, 3}
    # supplier enriched from the local map for the АНИС code
    assert next(i for i in tigani["items"] if i["article_id"] == 1)["supplier"] == SUP
    assert next(i for i in tigani["items"] if i["article_id"] == 1)["has_image"] is True

    # price range
    cheap = (await owner_client.get("/api/catalog/search?price_max=10")).json()
    assert [i["article_id"] for i in cheap["items"]] == [2]

    # low-only
    low = (await owner_client.get("/api/catalog/search?low_only=true")).json()
    assert [i["article_id"] for i in low["items"]] == [2]

    # sort by price desc
    byprice = (await owner_client.get("/api/catalog/search?sort=price&order=desc")).json()
    assert [i["article_id"] for i in byprice["items"]] == [3, 1, 2]


@pytest.mark.asyncio
async def test_attribute_filter_and_facets(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Ваза червена", "actual_price": "9",
         "article_details": {"1": {"key": "Цвят", "value": "Червен", "key_id": 1, "value_id": 5}}},
        {"article_id": 2, "code": "B", "article_name": "Ваза синя", "actual_price": "9",
         "article_details": {"1": {"key": "Цвят", "value": "Син", "key_id": 1, "value_id": 6}}},
    ])
    # attribute filter: only the red one
    red = (await owner_client.get("/api/catalog/search?attr=Цвят=Червен")).json()
    assert [i["article_id"] for i in red["items"]] == [1]
    assert red["items"][0]["attributes"][0] == {"key": "Цвят", "value": "Червен", "key_id": 1, "value_id": 5}
    # facets expose the attribute values for the dropdowns
    f = (await owner_client.get("/api/catalog/facets")).json()
    assert set(f["attributes"]["Цвят"]) == {"Червен", "Син"}


@pytest.mark.asyncio
async def test_facets_from_cache(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": EXIST_CODE, "article_name": "Тиган", "actual_price": "19.90"},
    ])
    f = (await owner_client.get("/api/catalog/facets")).json()
    assert SUP in f["suppliers"]


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    assert (await client.get("/api/catalog/search")).status_code == 401


@pytest.mark.asyncio
async def test_sync_requires_staff(packer_client):
    # packer is below staff — the sync (write) is gated
    assert (await packer_client.post("/api/catalog/sync")).status_code == 403
