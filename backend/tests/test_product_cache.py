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
async def test_bulk_price_preview_then_apply(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Едно", "actual_price": "10.00"},
        {"article_id": 2, "code": "B", "article_name": "Две", "actual_price": "20.00"},
    ])
    # preview: ×1.2, writes nothing
    prev = (await owner_client.post("/api/catalog/bulk-price",
            json={"article_ids": [1, 2], "op": "mul", "value": 1.2})).json()
    assert prev["dry_run"] is True
    by = {c["article_id"]: c for c in prev["changes"]}
    assert by[1]["old_price"] == 10 and by[1]["new_price"] == 12.0
    assert by[2]["new_price"] == 24.0
    assert "Articles_update" not in barsy_recorder.operations   # nothing written on preview

    # apply
    ap = (await owner_client.post("/api/catalog/bulk-price",
          json={"article_ids": [1, 2], "op": "mul", "value": 1.2, "confirm": True})).json()
    assert ap["applied"] == 2
    assert barsy_recorder.operations.count("Articles_update") == 2
    # cache updated (write-through) → search reflects the new price
    a1 = (await owner_client.get("/api/catalog/search?q=едно")).json()["items"][0]
    assert a1["price"] == 12.0


@pytest.mark.asyncio
async def test_bulk_price_requires_owner(staff_client):
    r = await staff_client.post("/api/catalog/bulk-price",
                                json={"article_ids": [1], "op": "set", "value": 5, "confirm": True})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    assert (await client.get("/api/catalog/search")).status_code == 401


@pytest.mark.asyncio
async def test_sync_requires_staff(packer_client):
    # packer is below staff — the sync (write) is gated
    assert (await packer_client.post("/api/catalog/sync")).status_code == 403
