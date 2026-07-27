# -*- coding: utf-8 -*-
"""Онлайн цени (/api/online-pricing): the channel-price surface. Sets a distinct ONLINE price per
article via a native Barsy rule scoped to the online delivery types, WITHOUT touching actual_price.
Asserts the price math, the dry-run/gate, and — critically — that every push re-asserts the
delivery-type scope (2+4 only) so an online rule can never bleed into physical prices."""
import pytest

from app.routers import online_pricing as op


@pytest.fixture(autouse=True)
def _clear_rule_cache():
    op._RULE_ID_CACHE.clear()
    yield
    op._RULE_ID_CACHE.clear()


def _live(monkeypatch):
    monkeypatch.setattr(op, "barsy_write_live", lambda: True)


def _last_body(rec, operation):
    """Body of the LAST recorded call to `operation` (body_for gives the first)."""
    for o, body in reversed(rec.calls):
        if o == operation:
            return body
    raise KeyError(operation)


def _seed_reads(rec, baselines):
    # the panel finds its existing rule by name (skip create path) …
    rec.set_return("Pricelists_index", [{"pricelist_id": 99, "pricelist_name": op._RULE_NAME}])
    # … and reads store prices via Articles_getlist (price_baselines)
    rec.set_return("Articles_getlist",
                   [{"article_id": aid, "actual_price": p} for aid, p in baselines.items()])


# ---------------------------------------------------------------- price math (pure) ---
def test_compute_exact():
    assert op._compute("exact", 24.9, None, 2) == "24.90"


def test_compute_pct_of_store():
    # online = store +10%  → 42 * 1.10 = 46.20
    assert op._compute("pct", 10, 42.0, 2) == "46.20"
    # a discount: store -5% → 42 * 0.95 = 39.90
    assert op._compute("pct", -5, 42.0, 2) == "39.90"


def test_compute_fixed_offset():
    assert op._compute("fixed", 2, 42.0, 2) == "44.00"


def test_compute_clear_is_none():
    assert op._compute("clear", 0, 42.0, 2) is None


def test_compute_relative_needs_base():
    with pytest.raises(ValueError):
        op._compute("pct", 10, None, 2)


def test_compute_refuses_non_positive():
    with pytest.raises(ValueError):
        op._compute("fixed", -100, 42.0, 2)


# ---------------------------------------------------------------- dry-run + gate ---
async def test_apply_dry_run_writes_nothing(owner_client, barsy_recorder):
    _seed_reads(barsy_recorder, {5: "42"})
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "pct", "value": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["count"] == 1
    assert body["preview"][0]["online_price"] == "46.20"
    assert "Pricelists_save_articles" not in barsy_recorder.operations   # nothing written


async def test_apply_gated_when_write_off(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(op, "barsy_write_live", lambda: False)     # force the master gate OFF
    _seed_reads(barsy_recorder, {5: "42"})
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "exact", "value": 30, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["gated"] is True
    assert "Pricelists_save_articles" not in barsy_recorder.operations


# ---------------------------------------------------------------- live writes ---
async def test_apply_live_writes_rule_and_scope(owner_client, barsy_recorder, monkeypatch):
    _live(monkeypatch)
    _seed_reads(barsy_recorder, {5: "42", 6: "10"})
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5, 6], "mode": "exact", "value": 30, "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False and body["applied"] == 2 and body["rule_id"] == 99
    # the per-article custom prices reached the rule
    saved = barsy_recorder.body_for("Pricelists_save_articles")
    assert saved["id"] == 99
    by_id = {row["article_id"]: row for row in saved["rows"]}
    assert by_id[5]["custom_price"] == "30.00" and by_id[6]["custom_price"] == "30.00"
    # SAFETY: the scope was (re-)asserted to the ONLINE delivery types only (2+4 checked, rest off)
    tax = barsy_recorder.body_for("Pricelists_save_tax_regions")
    checked = {int(rw["tax_region_id"]) for rw in tax["rows"] if rw["checked"]}
    assert checked == {2, 4}
    assert "Pricelists_set_status" in barsy_recorder.operations


async def test_apply_live_pct_uses_store_baseline(owner_client, barsy_recorder, monkeypatch):
    _live(monkeypatch)
    _seed_reads(barsy_recorder, {5: "42"})
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "pct", "value": 10, "confirm": True})
    assert r.status_code == 200, r.text
    saved = barsy_recorder.body_for("Pricelists_save_articles")
    assert saved["rows"][0]["custom_price"] == "46.20"     # 42 +10%


async def test_clear_removes_from_pushed_set(owner_client, barsy_recorder, monkeypatch):
    _live(monkeypatch)
    _seed_reads(barsy_recorder, {5: "42", 6: "10"})
    # set two online prices
    await owner_client.post("/api/online-pricing/apply",
                            json={"article_ids": [5, 6], "mode": "exact", "value": 30, "confirm": True})
    # clear one → the full pushed set must now contain only the other
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "clear", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["total_online"] == 1
    ids = {row["article_id"] for row in _last_body(barsy_recorder, "Pricelists_save_articles")["rows"]}
    assert ids == {6}                                       # 5 was removed by omission


async def test_apply_relative_with_no_baseline_is_uncovered(owner_client, barsy_recorder, monkeypatch):
    _live(monkeypatch)
    _seed_reads(barsy_recorder, {})            # Articles_getlist returns no rows → no baseline
    r = await owner_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "pct", "value": 10, "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 0 and 5 in body["uncovered"]


# ---------------------------------------------------------------- list + auth ---
async def test_list_empty(owner_client, barsy_recorder):
    r = await owner_client.get("/api/online-pricing")
    assert r.status_code == 200 and r.json()["count"] == 0


async def test_apply_requires_owner(staff_client):
    r = await staff_client.post("/api/online-pricing/apply",
                                json={"article_ids": [5], "mode": "exact", "value": 30})
    assert r.status_code in (401, 403)
