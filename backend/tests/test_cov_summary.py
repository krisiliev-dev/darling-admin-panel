# -*- coding: utf-8 -*-
"""Nav badges (/api/summary/badges) — pending-work counts keyed by nav route.
  * /intake -> approved IntakeDraft rows (the panel's own async DB, deterministic)
  * /orders -> fulfillment_state rows in new/picking/packed (pipeline sqlite)
  * /cod    -> delivered rows with cod_amount>0 (pipeline sqlite)
  * /issues -> unresolved ProcessingIssue rows (panel async DB)

The fulfillment counts read the shared pipeline sqlite through db.connect(); we point that
at a throwaway temp DB so the counts are deterministic and no real pipeline.db is touched.
"""
import sqlite3

import app.routers.summary as summary


def _fake_connect_factory(tmp_path, rows):
    """A db.connect() replacement bound to a throwaway sqlite carrying fulfillment_state."""
    dbfile = tmp_path / "pipeline_stub.db"
    con = sqlite3.connect(str(dbfile))
    con.execute("CREATE TABLE fulfillment_state(order_id TEXT PRIMARY KEY, order_name TEXT, "
                "status TEXT, courier TEXT, tracking TEXT, cod_amount REAL, label_ref TEXT, "
                "updated_ts TEXT, note TEXT)")
    con.executemany("INSERT INTO fulfillment_state(order_id, status, cod_amount) VALUES (?,?,?)",
                    rows)
    con.commit()
    con.close()
    return lambda: sqlite3.connect(str(dbfile))


async def test_badges_counts_all_three_routes(owner_client, make_draft, monkeypatch, tmp_path):
    # two approved drafts + one already-pushed (pushed must NOT count toward /intake)
    await make_draft(code="A", name="Едно", status="approved")
    await make_draft(code="B", name="Две", status="approved")
    await make_draft(code="C", name="Три", status="pushed")
    monkeypatch.setattr(summary.db, "connect", _fake_connect_factory(tmp_path, [
        ("1", "new", 0), ("2", "picking", 0), ("3", "packed", 0),   # 3 orders_todo
        ("4", "delivered", 25.0),                                   # cod awaiting
        ("5", "delivered", 0),                                      # delivered but no COD -> excluded
        ("6", "cod_paid", 40.0),                                    # already paid -> excluded
    ]))
    r = await owner_client.get("/api/summary/badges")
    assert r.status_code == 200, r.text
    assert r.json() == {"/intake": 2, "/orders": 3, "/cod": 1, "/issues": 0}


async def test_badges_empty_state_is_zeros(owner_client, monkeypatch, tmp_path):
    monkeypatch.setattr(summary.db, "connect", _fake_connect_factory(tmp_path, []))
    r = await owner_client.get("/api/summary/badges")
    assert r.status_code == 200
    assert r.json() == {"/intake": 0, "/orders": 0, "/cod": 0, "/issues": 0}


async def test_badges_survive_missing_fulfillment_table(owner_client, monkeypatch, tmp_path):
    # _fulfillment_counts swallows errors -> orders/cod fall back to 0, /intake still counted
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    monkeypatch.setattr(summary.db, "connect", lambda: sqlite3.connect(str(empty)))
    r = await owner_client.get("/api/summary/badges")
    assert r.status_code == 200
    assert r.json() == {"/intake": 0, "/orders": 0, "/cod": 0, "/issues": 0}


async def test_badges_readable_by_lowest_role(packer_client, monkeypatch, tmp_path):
    # badges are gated by current_active_user only — even a packer may read them
    monkeypatch.setattr(summary.db, "connect", _fake_connect_factory(tmp_path, []))
    r = await packer_client.get("/api/summary/badges")
    assert r.status_code == 200


async def test_badges_require_auth(client):
    r = await client.get("/api/summary/badges")
    assert r.status_code == 401
