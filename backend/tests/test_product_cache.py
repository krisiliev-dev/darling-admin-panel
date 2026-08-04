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
async def test_granular_filters(owner_client, barsy_recorder):
    """The catalogue search supports brand / description-contains / stock-range / missing-data filters
    over the cache, and facets expose brands."""
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Тиган", "actual_price": "20",
         "brand": "Tefal", "description": "Тефлоново покритие", "picture_url": "/x.png", "barcode": "111"},
        {"article_id": 2, "code": "B", "article_name": "Чиния", "actual_price": "5",
         "brand": "Luminarc", "description": "Стъклена чиния"},
    ], stock_rows=[{"article_id": 1, "store_amount": "10", "min_amount": "3"},
                   {"article_id": 2, "store_amount": "0", "min_amount": "1", "below_min": 1}])
    async def ids(qs):
        return [i["article_id"] for i in (await owner_client.get(f"/api/catalog/search?{qs}")).json()["items"]]
    assert await ids("brand=Tefal") == [1]
    assert await ids("desc=стъклена") == [2]                 # description contains (Cyrillic-safe)
    assert await ids("has_image=false") == [2]               # no picture
    assert await ids("has_barcode=false") == [2]             # no barcode
    assert await ids("stock_min=5") == [1]                   # stock range
    f = (await owner_client.get("/api/catalog/facets")).json()
    assert set(f["brands"]) == {"Tefal", "Luminarc"}


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
    assert barsy_recorder.operations.count("articles_index") == 1   # one native save_mass, not 2 updates
    # cache updated (write-through) → search reflects the new price
    a1 = (await owner_client.get("/api/catalog/search?q=едно")).json()["items"][0]
    assert a1["price"] == 12.0


@pytest.mark.asyncio
async def test_bulk_price_large_batch_runs_as_background_job(owner_client, barsy_recorder):
    """G4c: a batch over the sync cap returns {async, job_id} and runs off-request; polling the job
    endpoint shows it finish, and every article is still written + the cache updated."""
    import asyncio
    arts = [{"article_id": i, "code": f"C{i}", "article_name": f"Art{i}", "actual_price": "10.00"}
            for i in range(1, 36)]                                   # 35 > _SYNC_CAP (30)
    await _sync(owner_client, barsy_recorder, arts)
    ids = [a["article_id"] for a in arts]
    r = (await owner_client.post("/api/catalog/bulk-price",
         json={"article_ids": ids, "op": "mul", "value": 1.1, "confirm": True})).json()
    assert r["async"] is True and r["job_id"]                        # not applied inline
    job_id = r["job_id"]
    for _ in range(150):                                             # pacing ~0.12s/item → a few seconds
        j = (await owner_client.get(f"/api/catalog/bulk-jobs/{job_id}")).json()
        if j["status"] != "running":
            break
        await asyncio.sleep(0.2)
    assert j["status"] == "done", j
    assert j["applied"] == 35 and j["total"] == 35 and j["done"] == 35
    assert barsy_recorder.operations.count("articles_index") == 1   # 35 cells in one save_mass
    # cache write-through happened in the background session
    a1 = (await owner_client.get("/api/catalog/search?q=Art1&per_page=1")).json()["items"][0]
    assert a1["price"] == 11.0


@pytest.mark.asyncio
async def test_bulk_job_404_for_unknown_id(owner_client):
    assert (await owner_client.get("/api/catalog/bulk-jobs/nope")).status_code == 404


@pytest.mark.asyncio
async def test_naming_recipe_roundtrip(owner_client):
    opts = (await owner_client.get("/api/intake/recipe/options")).json()["options"]
    assert "Color" in opts and "suffix" in opts["Color"]["variants"]        # recipe knobs available
    r = await owner_client.put("/api/intake/recipe?leaf=hg-1", json={"style": {"Color": "suffix"}})
    assert r.status_code == 200, r.text
    g = (await owner_client.get("/api/intake/recipe?leaf=hg-1")).json()
    assert g["leaf"].get("Color") == "suffix" and g["effective"].get("Color") == "suffix"


@pytest.mark.asyncio
async def test_bulk_rename_recomposes(owner_client, barsy_recorder):
    from app.db import async_session_maker
    from app.models import ProductCache
    cats = (await owner_client.get("/api/intake/shopify-categories", params={"q": "Купи"})).json()["categories"]
    leaf = next((c["id"] for c in cats if c["path"].endswith("Купи")), None)
    assert leaf, "expected a 'Купи' leaf in the taxonomy"
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "старо име", "actual_price": "9",
         "article_details": {"1": {"key": "Материал", "value": "Керамика", "key_id": 1, "value_id": 5}}},
    ])
    async with async_session_maker() as s:                       # sync doesn't carry the Shopify leaf; set it
        p = await s.get(ProductCache, 1)
        p.shopify_category_id = leaf
        await s.commit()
    prev = (await owner_client.post("/api/catalog/bulk-rename", json={"article_ids": [1]})).json()
    assert prev["dry_run"] is True and prev["changes"]
    assert "керамичн" in prev["changes"][0]["new_name"].lower()   # recomposed via the v3 recipe
    barsy_recorder.set_return("Articles_update", 1)
    ap = (await owner_client.post("/api/catalog/bulk-rename", json={"article_ids": [1], "confirm": True})).json()
    assert ap["applied"] == 1 and barsy_recorder.operations.count("Articles_update") == 1


@pytest.mark.asyncio
async def test_bulk_price_decimal_rounding(owner_client, barsy_recorder):
    """Money math is exact-Decimal ROUND_HALF_UP — not binary float + round() (which gives 2.67)."""
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Едно", "actual_price": "5.35"},
    ])
    prev = (await owner_client.post("/api/catalog/bulk-price",
            json={"article_ids": [1], "op": "mul", "value": 0.5})).json()
    assert prev["changes"][0]["new_price"] == 2.68        # 5.35×0.5 = 2.675 → half-up → 2.68


@pytest.mark.asyncio
async def test_bulk_price_skips_free_and_baseless(owner_client, barsy_recorder):
    """A relative op must never make a product free/negative, and must refuse a product with no
    baseline price — those are reported as `skipped`, never written."""
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Едно", "actual_price": "10.00"},
        {"article_id": 2, "code": "B", "article_name": "Без цена"},   # no actual_price → price is None
    ])
    prev = (await owner_client.post("/api/catalog/bulk-price",
            json={"article_ids": [1, 2], "op": "sub", "value": 10})).json()
    assert prev["changes"] == []                          # both refused
    skipped = {s["article_id"] for s in prev["skipped"]}
    assert skipped == {1, 2}                              # id1: would be ≤0; id2: no baseline
    # confirm writes nothing
    ap = (await owner_client.post("/api/catalog/bulk-price",
          json={"article_ids": [1, 2], "op": "sub", "value": 10, "confirm": True})).json()
    assert ap["applied"] == 0 and "articles_index" not in barsy_recorder.operations


@pytest.mark.asyncio
async def test_bulk_price_rejects_oversized_selection(owner_client):
    """The selection is capped so a bulk op can't fan out into thousands of serial Barsy writes."""
    r = await owner_client.post("/api/catalog/bulk-price",
                                json={"article_ids": list(range(3000)), "op": "mul", "value": 1.1})
    assert r.status_code == 422                           # pydantic max_length


@pytest.mark.asyncio
async def test_promo_rejects_bad_date(owner_client, barsy_recorder):
    r = await owner_client.post("/api/catalog/promo", json={
        "article_ids": [1], "name": "x", "mode": "discount_pct", "value": 10,
        "date_from": "15-12-2026", "date_to": "2026-12-31", "confirm": True})
    assert r.status_code == 400                           # not ГГГГ-ММ-ДД


@pytest.mark.asyncio
async def test_promo_creates_pricelist_and_attaches(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Едно", "actual_price": "10"},
        {"article_id": 2, "code": "B", "article_name": "Две", "actual_price": "20"},
    ])
    barsy_recorder.set_return("Pricelists_edit", 1)
    # the id is resolved by diffing the pricelist index before vs after the save: empty before,
    # the new promo (42) after — so it's picked as the genuinely-new row.
    idx_calls = {"n": 0}

    def _idx(_body):
        idx_calls["n"] += 1
        return {"rows": []} if idx_calls["n"] == 1 else {"rows": [{"pricelist_id": 42, "pricelist_name": "Коледа"}]}
    barsy_recorder.set_return("Pricelists_index", _idx)
    barsy_recorder.set_return("Pricelists_save_articles", 1)
    # preview
    prev = (await owner_client.post("/api/catalog/promo", json={
        "article_ids": [1, 2], "name": "Коледа", "mode": "discount_pct", "value": 20,
        "date_from": "2026-12-15", "date_to": "2026-12-31"})).json()
    assert prev["dry_run"] is True and prev["period"] == "15.12-31.12"
    assert "Pricelists_edit" not in barsy_recorder.operations
    # apply
    r = (await owner_client.post("/api/catalog/promo", json={
        "article_ids": [1, 2], "name": "Коледа", "mode": "discount_pct", "value": 20,
        "date_from": "2026-12-15", "date_to": "2026-12-31", "confirm": True})).json()
    assert r["pricelist_id"] == 42 and r["count"] == 2
    body = barsy_recorder.body_for("Pricelists_edit")
    assert body["values"]["modificator_type"] == 2 and body["values"]["periods_date"] == "15.12-31.12"
    attach = barsy_recorder.body_for("Pricelists_save_articles")
    assert {row["article_id"] for row in attach["rows"]} == {1, 2}


@pytest.mark.asyncio
async def test_promo_id_resolves_new_row_when_name_duplicated(owner_client, barsy_recorder):
    """G7: an OLD promo with the same name already exists — the id must resolve to the NEW row
    (99), not the pre-existing 42, via the before/after index diff."""
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Едно", "actual_price": "10"}])
    barsy_recorder.set_return("Pricelists_edit", 1)
    barsy_recorder.set_return("Pricelists_save_articles", 1)
    old = {"pricelist_id": 42, "pricelist_name": "Коледа"}
    new = {"pricelist_id": 99, "pricelist_name": "Коледа"}
    idx_calls = {"n": 0}

    def _idx(_body):
        idx_calls["n"] += 1
        return {"rows": [old]} if idx_calls["n"] == 1 else {"rows": [old, new]}   # 99 appeared after save
    barsy_recorder.set_return("Pricelists_index", _idx)
    r = (await owner_client.post("/api/catalog/promo", json={
        "article_ids": [1], "name": "Коледа", "mode": "discount_pct", "value": 20,
        "date_from": "2026-12-15", "date_to": "2026-12-31", "confirm": True})).json()
    assert r["pricelist_id"] == 99                       # the freshly-created row, not the old 42
    assert barsy_recorder.body_for("Pricelists_save_articles")["id"] == 99


@pytest.mark.asyncio
async def test_list_and_delete_promos(owner_client, barsy_recorder):
    barsy_recorder.set_return("Pricelists_index", {"rows": [
        {"pricelist_id": 42, "pricelist_name": "Коледа", "periods_date": "15.12-31.12"}]})
    barsy_recorder.set_return("Pricelists_delete", 1)
    lst = (await owner_client.get("/api/catalog/promos")).json()["promos"]
    assert lst == [{"id": 42, "name": "Коледа", "period": "15.12-31.12", "active": True}]
    d = await owner_client.post("/api/catalog/promos/42/delete", json={})
    assert d.status_code == 200 and d.json()["ok"] is True
    assert "Pricelists_delete" in barsy_recorder.operations


@pytest.mark.asyncio
async def test_promo_requires_owner(staff_client):
    r = await staff_client.post("/api/catalog/promo", json={
        "article_ids": [1], "name": "x", "mode": "discount_pct", "value": 10,
        "date_from": "2026-12-15", "date_to": "2026-12-31", "confirm": True})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_price_search_matches_number(owner_client, barsy_recorder):
    await _sync(owner_client, barsy_recorder, [
        {"article_id": 1, "code": "A", "article_name": "Чаша", "actual_price": "19.90"},
        {"article_id": 2, "code": "B", "article_name": "Чиния", "actual_price": "4.50"},
    ])
    r = (await owner_client.get("/api/catalog/search?q=19.90")).json()
    assert [i["article_id"] for i in r["items"]] == [1]   # a price search finds the product


@pytest.mark.asyncio
async def test_bulk_price_requires_owner(staff_client):
    r = await staff_client.post("/api/catalog/bulk-price",
                                json={"article_ids": [1], "op": "set", "value": 5, "confirm": True})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_refresh_cache_for_code_upserts(owner_client, barsy_recorder):
    """The webhook write-through: re-fetch one article and upsert its cache row."""
    from app.routers.catalog import refresh_cache_for_code
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 77, "code": "WH-1", "article_name": "Уебхук продукт", "actual_price": "5.00"}])
    assert await refresh_cache_for_code("WH-1") is True
    r = (await owner_client.get("/api/catalog/search?q=уебхук")).json()
    assert [i["article_id"] for i in r["items"]] == [77]


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    assert (await client.get("/api/catalog/search")).status_code == 401


@pytest.mark.asyncio
async def test_sync_requires_staff(packer_client):
    # packer is below staff — the sync (write) is gated
    assert (await packer_client.post("/api/catalog/sync")).status_code == 403


@pytest.mark.asyncio
async def test_sync_populates_shopify_leaf_from_pipeline(owner_client, barsy_recorder, monkeypatch):
    """Regression: the cache sync must populate ProductCache.shopify_category_id from the enriched
    pipeline record's leaf — without it, bulk-rename skipped EVERY product as "no category"
    (review 2026-07-26). Here we stub the one-pass leaf map."""
    from app.db import async_session_maker
    from app.models import ProductCache
    from app.routers import catalog
    monkeypatch.setattr(catalog, "_leaf_map", lambda: {"К-9": "hg-1-15"})
    art = {"article_id": 9, "code": "К-9", "article_name": "Стъклена купа", "actual_price": "5.00",
           "brand": "Luminarc",
           "article_details": [{"detail_name": "Материал", "detail_value": "стъкло"}]}
    await _sync(owner_client, barsy_recorder, [art])
    async with async_session_maker() as s:
        p = await s.get(ProductCache, 9)
        assert p is not None and p.shopify_category_id == "hg-1-15"   # leaf carried onto the cache row


def test_leaf_from_gid_extracts_taxonomy_id():
    from app.routers.catalog import _leaf_from_gid
    assert _leaf_from_gid("gid://shopify/TaxonomyCategory/hg-11-2") == "hg-11-2"
    assert _leaf_from_gid("") == ""
