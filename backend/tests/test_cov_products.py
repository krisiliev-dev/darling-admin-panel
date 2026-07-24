# -*- coding: utf-8 -*-
"""Каталог list (/api/products) — read-only window onto the pipeline's dim_product, joined to
products (needs_human), the latest price_history row, and launch_readiness.

Reads the pipeline sqlite through db.connect(); we point that at a throwaway DB for
deterministic rows and to exercise the search filter + pagination.
"""
import sqlite3

import pytest

import app.routers.products as products


def _build_db(tmp_path):
    dbfile = tmp_path / "products.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        """
        CREATE TABLE dim_product(code TEXT, name TEXT, brand TEXT, category_leaf TEXT, supplier TEXT);
        CREATE TABLE products(code TEXT, needs_human INTEGER);
        CREATE TABLE price_history(code TEXT, snapshot_date TEXT, retail_price REAL);
        CREATE TABLE launch_readiness(code TEXT, ready INTEGER);
        """)
    con.executemany("INSERT INTO dim_product VALUES (?,?,?,?,?)", [
        ("100", "Ароматна свещ", "Бранд", "Свещи", "Алфа"),
        ("200", "Бяла чиния", "", "Съдове", "Бета"),
        ("300", "Влакно за под", "", "Почистване", ""),
    ])
    con.executemany("INSERT INTO products VALUES (?,?)", [("100", 1), ("200", 0)])
    con.executemany("INSERT INTO price_history VALUES (?,?,?)", [
        ("100", "2026-07-01", 5.0), ("100", "2026-07-20", 6.5), ("200", "2026-07-10", 9.0)])
    con.executemany("INSERT INTO launch_readiness VALUES (?,?)", [("100", 1), ("200", 0)])
    con.commit()
    con.close()
    return lambda: sqlite3.connect(str(dbfile))


async def test_list_products_maps_rows(owner_client, monkeypatch, tmp_path):
    monkeypatch.setattr(products.db, "connect", _build_db(tmp_path))
    r = await owner_client.get("/api/products")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["limit"] == 50 and body["offset"] == 0
    items = {i["code"]: i for i in body["items"]}
    # latest price wins for code 100
    assert items["100"]["price"] == 6.5
    assert items["100"]["needs_human"] is True
    assert items["100"]["launch_ready"] is True
    assert items["200"]["launch_ready"] is False
    # no products/launch_readiness row for 300 -> needs_human False, launch_ready None
    assert items["300"]["needs_human"] is False
    assert items["300"]["launch_ready"] is None
    assert items["200"]["brand"] == ""   # NULL brand -> empty string
    # ordered by name
    assert [i["code"] for i in body["items"]] == ["100", "200", "300"]


async def test_list_products_search_filters_code_and_name(owner_client, monkeypatch, tmp_path):
    monkeypatch.setattr(products.db, "connect", _build_db(tmp_path))
    # match by name substring
    r = await owner_client.get("/api/products", params={"q": "чиния"})
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["code"] == "200"
    # match by code substring
    r2 = await owner_client.get("/api/products", params={"q": "300"})
    assert [i["code"] for i in r2.json()["items"]] == ["300"]


async def test_list_products_pagination(owner_client, monkeypatch, tmp_path):
    monkeypatch.setattr(products.db, "connect", _build_db(tmp_path))
    r = await owner_client.get("/api/products", params={"limit": 1, "offset": 1})
    body = r.json()
    # total is the full count; the page is bounded by limit/offset (2nd row by name)
    assert body["total"] == 3
    assert body["limit"] == 1 and body["offset"] == 1
    assert [i["code"] for i in body["items"]] == ["200"]


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "999"}, {"offset": "-1"}])
async def test_list_products_rejects_bad_paging(owner_client, monkeypatch, tmp_path, params):
    monkeypatch.setattr(products.db, "connect", _build_db(tmp_path))
    r = await owner_client.get("/api/products", params=params)
    assert r.status_code == 422


async def test_list_products_requires_auth(client):
    r = await client.get("/api/products")
    assert r.status_code == 401
