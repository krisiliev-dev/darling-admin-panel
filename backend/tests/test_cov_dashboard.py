# -*- coding: utf-8 -*-
"""Анализи (/api/dashboard) — catalog readiness (LIVE numbers) + sales KPIs (empty until
launch).

/catalog reads the pipeline sqlite through db.connect(); we point that at a throwaway DB so
the card numbers are deterministic. The handler's _one() helper is defensively wrapped
(missing table -> default), which we also assert. /sales is pure analytics — we stub the
three analytics calls.
"""
import sqlite3

import pytest

import app.routers.dashboard as dashboard


def _build_catalog_db(tmp_path):
    dbfile = tmp_path / "catalog.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        """
        CREATE TABLE products(code TEXT, needs_human INTEGER);
        CREATE TABLE launch_readiness(code TEXT, ready INTEGER);
        CREATE TABLE review_state(code TEXT, ready INTEGER);
        CREATE TABLE dim_product(code TEXT, supplier TEXT);
        CREATE TABLE price_history(code TEXT, snapshot_date TEXT, retail_price REAL);
        """)
    con.executemany("INSERT INTO products VALUES (?,?)",
                    [("p1", 1), ("p2", 0), ("p3", 1), ("p4", 0), ("p5", 0)])   # 5 total, 2 need human
    con.executemany("INSERT INTO launch_readiness VALUES (?,?)",
                    [("p1", 1), ("p2", 1), ("p3", 0)])                          # 2 ready, 1 blocked
    con.executemany("INSERT INTO review_state VALUES (?,?)",
                    [("p1", 1), ("p2", 0)])                                     # 2 reviewed, 1 ready
    con.executemany("INSERT INTO dim_product VALUES (?,?)",
                    [("p1", "Алфа"), ("p2", "Алфа"), ("p3", "Бета"), ("p4", "")])
    con.executemany("INSERT INTO price_history VALUES (?,?,?)",
                    [("p1", "2026-07-01", 10.0), ("p1", "2026-07-20", 12.0),
                     ("p2", "2026-07-20", 20.0)])                               # latest avg = 16.0
    con.commit()
    con.close()
    return lambda: sqlite3.connect(str(dbfile))


async def test_catalog_readiness_cards(owner_client, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard.db, "connect", _build_catalog_db(tmp_path))
    r = await owner_client.get("/api/dashboard/catalog")
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    assert cards["total"] == 5
    assert cards["needs_human"] == 2
    assert cards["launch_ready"] == 2
    assert cards["launch_blocked"] == 1
    # unscored = total - (ready+blocked) = 5 - 3
    assert cards["unscored"] == 2
    assert cards["reviewed"] == 2
    assert cards["review_ready"] == 1
    # supplier '' is excluded; Алфа(2) + Бета(1) -> 2 distinct suppliers
    assert cards["suppliers"] == 2
    assert cards["latest_snapshot"] == "2026-07-20"
    # avg over the latest snapshot only: (12.0 + 20.0)/2
    assert cards["avg_retail"] == 16.0
    # readiness funnel sums back to the cards
    funnel = {row["tone"]: row["value"] for row in r.json()["readiness"]}
    assert funnel == {"ready": 2, "blocked": 1, "pending": 2}
    # suppliers list is name+count, biggest first
    assert r.json()["suppliers"][0] == {"supplier": "Алфа", "count": 2}


async def test_catalog_readiness_defaults_on_empty_schema(owner_client, monkeypatch, tmp_path):
    # tables present but empty -> the _one()-guarded cards degrade to their defaults
    dbfile = tmp_path / "emptyschema.db"
    con = sqlite3.connect(str(dbfile))
    con.executescript(
        "CREATE TABLE products(code TEXT, needs_human INTEGER);"
        "CREATE TABLE launch_readiness(code TEXT, ready INTEGER);"
        "CREATE TABLE review_state(code TEXT, ready INTEGER);"
        "CREATE TABLE dim_product(code TEXT, supplier TEXT);"
        "CREATE TABLE price_history(code TEXT, snapshot_date TEXT, retail_price REAL);")
    con.commit()
    con.close()
    monkeypatch.setattr(dashboard.db, "connect", lambda: sqlite3.connect(str(dbfile)))
    r = await owner_client.get("/api/dashboard/catalog")
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert cards["total"] == 0 and cards["launch_ready"] == 0
    assert cards["suppliers"] == 0
    assert cards["latest_snapshot"] is None


async def test_catalog_readiness_survives_missing_tables(owner_client, monkeypatch, tmp_path):
    # FIXED: the suppliers query is now guarded like its siblings — a totally empty DB (no tables)
    # returns zeros/[] instead of a 500.
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    monkeypatch.setattr(dashboard.db, "connect", lambda: sqlite3.connect(str(empty)))
    r = await owner_client.get("/api/dashboard/catalog")
    assert r.status_code == 200
    assert r.json()["cards"]["suppliers"] == 0


async def test_catalog_readiness_requires_auth(client):
    r = await client.get("/api/dashboard/catalog")
    assert r.status_code == 401


async def test_sales_shape_from_analytics(owner_client, monkeypatch):
    monkeypatch.setattr(dashboard.analytics, "kpis", lambda: {"orders_total": 0, "revenue_total": 0})
    monkeypatch.setattr(dashboard.analytics, "top_products", lambda **k: [])
    monkeypatch.setattr(dashboard.analytics, "price_changes", lambda **k: [])
    r = await owner_client.get("/api/dashboard/sales")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"kpis", "top_products", "price_changes"}
    assert body["kpis"]["orders_total"] == 0
    assert body["top_products"] == [] and body["price_changes"] == []


async def test_sales_requires_auth(client):
    r = await client.get("/api/dashboard/sales")
    assert r.status_code == 401


async def test_catalog_readable_by_lowest_role(packer_client, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard.db, "connect", _build_catalog_db(tmp_path))
    r = await packer_client.get("/api/dashboard/catalog")
    assert r.status_code == 200
