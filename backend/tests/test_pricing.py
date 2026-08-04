# -*- coding: utf-8 -*-
"""Cost-plus-markup repricing: set a markup, detect a supplier cost change, owner applies the new
retail to Barsy. The Barsy client is the recording stub (barsy_recorder) — no real ERP calls."""
import pytest


def _articles(delivery_price):
    return [{"article_id": 1, "code": "0151086", "article_name": "Чаша",
             "delivery_price": delivery_price, "actual_price": 1.5}]


async def test_set_rule_records_baseline_and_proposes(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", lambda b: {"article_id": b["article_id"], "delivery_price": 1.0, "actual_price": 1.5})
    r = await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 0.5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cost"] == 1.0 and body["proposed_retail"] == 1.5  # 1.00 +50% = 1.50


async def test_review_flags_when_cost_rises(owner_client, barsy_recorder):
    # price it at cost 1.00 +50% (retail already 1.50 → in line)
    barsy_recorder.set_return("Articles_get", lambda b: {"article_id": b["article_id"], "delivery_price": 1.0, "actual_price": 1.5})
    await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 0.5})

    # supplier cost rises to 1.50 → proposed retail should be 2.25, and the row must be flagged
    barsy_recorder.set_return("Articles_getlist", _articles(1.5))
    r = await owner_client.get("/api/pricing/review?only_flagged=true")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["cost"] == 1.5 and it["proposed_retail"] == 2.25
    assert it["needs_apply"] is True and it["cost_changed"] is True


async def test_apply_writes_new_retail_to_barsy(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", lambda b: {"article_id": b["article_id"], "delivery_price": 1.5, "actual_price": 1.5})
    await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 0.5})

    r = await owner_client.post("/api/pricing/apply", json={"article_id": 1, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["new_retail"] == 2.25  # 1.50 +50%
    # the Barsy write happened with the new price
    body = barsy_recorder.body_for("Articles_update")
    assert body["article"]["actual_price"] == 2.25


async def test_apply_requires_confirm(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", lambda b: {"article_id": b["article_id"], "delivery_price": 1.0, "actual_price": 1.5})
    await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 0.5})
    r = await owner_client.post("/api/pricing/apply", json={"article_id": 1, "confirm": False})
    assert r.status_code == 400


async def test_apply_is_owner_only(staff_client, barsy_recorder):
    r = await staff_client.post("/api/pricing/apply", json={"article_id": 1, "confirm": True})
    assert r.status_code in (401, 403)


async def test_rule_rejects_absurd_markup(owner_client, barsy_recorder):
    r = await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 99})
    assert r.status_code == 422  # capped at +1000%


# --- supplier-based (group) rules ---------------------------------------------------
from app.routers import pricing as pricing_mod  # noqa: E402


def test_propose_rounds_half_up_in_decimal_not_float():
    """Regression: _propose must compute the markup in Decimal, then ROUND_HALF_UP. The old
    float-first form (`cost*(1+markup)`) yielded 4.35×1.5 = 6.524999… and rounded DOWN to 6.52;
    the correct half-up of the exact 6.525 is 6.53 (analytics/pricing review 2026-07-26)."""
    assert pricing_mod._propose(4.35, 0.5) == 6.53
    # a clean case is unaffected
    assert pricing_mod._propose(10.0, 0.2) == 12.00
    # half-up on an exact boundary rounds up
    assert pricing_mod._propose(2.505, 0.0) == 2.51


@pytest.fixture
def mock_supplier(monkeypatch):
    def _apply(code_to_supplier):
        monkeypatch.setattr(pricing_mod, "_supplier_map", lambda: dict(code_to_supplier))
        monkeypatch.setattr(pricing_mod, "_supplier_for", lambda c: code_to_supplier.get(str(c), ""))
        names = sorted(set(code_to_supplier.values()))
        monkeypatch.setattr(pricing_mod, "_supplier_counts",
                            lambda: [{"name": n, "product_count": sum(1 for v in code_to_supplier.values() if v == n)} for n in names])
    return _apply


async def test_supplier_rule_resolves_in_review(owner_client, barsy_recorder, mock_supplier):
    mock_supplier({"AA": "Вертекс ООД"})
    await owner_client.post("/api/pricing/group", json={"scope": "supplier", "key": "Вертекс ООД", "markup_pct": 0.6})
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "AA", "article_name": "Кутия", "delivery_price": 2.0, "actual_price": 2.0}])
    r = await owner_client.get("/api/pricing/review?only_flagged=true")
    it = r.json()["items"][0]
    assert it["supplier"] == "Вертекс ООД" and it["markup_source"] == "supplier"
    assert it["target_markup"] == 0.6 and it["proposed_retail"] == 3.2  # 2.00 +60%
    assert it["needs_apply"] is True


async def test_product_rule_wins_over_supplier(owner_client, barsy_recorder, mock_supplier):
    mock_supplier({"AA": "Вертекс ООД"})
    await owner_client.post("/api/pricing/group", json={"scope": "supplier", "key": "Вертекс ООД", "markup_pct": 0.6})
    barsy_recorder.set_return("Articles_get", lambda b: {"article_id": b["article_id"], "code": "AA", "delivery_price": 2.0, "actual_price": 2.0})
    await owner_client.post("/api/pricing/rule", json={"article_id": 1, "markup_pct": 1.0})  # product override
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "AA", "article_name": "Кутия", "delivery_price": 2.0, "actual_price": 2.0}])
    it = (await owner_client.get("/api/pricing/review")).json()["items"][0]
    assert it["markup_source"] == "product" and it["target_markup"] == 1.0


async def test_apply_supplier_bulk_writes_all(owner_client, barsy_recorder, mock_supplier):
    mock_supplier({"AA": "Мира-Н", "BB": "Мира-Н"})
    await owner_client.post("/api/pricing/group", json={"scope": "supplier", "key": "Мира-Н", "markup_pct": 0.5})
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "AA", "delivery_price": 2.0, "actual_price": 2.0},
        {"article_id": 2, "code": "BB", "delivery_price": 4.0, "actual_price": 4.0}])
    r = await owner_client.post("/api/pricing/apply-supplier", json={"supplier": "Мира-Н", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 2
    saves = [op for op in barsy_recorder.operations if op == "articles_index"]
    assert len(saves) == 1                              # one native save_mass for both products
    vals = barsy_recorder.body_for("articles_index")["values"]
    assert len(vals) == 2                               # two cells: AA and BB actual_price
    # the affected products come back so the panel can offer new-price shop-floor labels
    changed = {c["code"]: c["price"] for c in body["changed"]}
    assert changed == {"AA": 3.0, "BB": 6.0}       # cost ×1.5 markup


async def test_suppliers_list_includes_markup(owner_client, mock_supplier):
    mock_supplier({"AA": "Егера ЕООД", "BB": "Егера ЕООД"})
    await owner_client.post("/api/pricing/group", json={"scope": "supplier", "key": "Егера ЕООД", "markup_pct": 0.4})
    r = await owner_client.get("/api/pricing/suppliers")
    egera = next(s for s in r.json()["suppliers"] if s["name"] == "Егера ЕООД")
    assert egera["product_count"] == 2 and egera["markup_pct"] == 0.4


async def test_apply_supplier_requires_owner(staff_client, mock_supplier):
    r = await staff_client.post("/api/pricing/apply-supplier", json={"supplier": "X", "confirm": True})
    assert r.status_code in (401, 403)
