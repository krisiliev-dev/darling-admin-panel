# -*- coding: utf-8 -*-
"""COD settle + overview — the money-moving path. Covers idempotency, discrepancy flagging, the
settlement-before-status-flip durability ordering (H1), and order-courier precedence (M1)."""
import pytest
from sqlalchemy import select

from app.db import async_session_maker
from app.models import CodSettlement
from app.routers import cod


@pytest.fixture
def settle_state(monkeypatch):
    """Two delivered COD orders awaiting settlement, on different carriers. The mock set_state
    mutates the state map (so GET /api/cod reflects the flip) and records calls."""
    state = {
        "o1": {"order_name": "#1", "status": "delivered", "courier": "econt", "tracking": "111", "cod_amount": 30.0},
        "o2": {"order_name": "#2", "status": "delivered", "courier": "speedy", "tracking": "222", "cod_amount": 20.0},
    }
    calls: list = []

    def _set(oid, name, **k):
        calls.append((oid, k))
        if "status" in k and oid in state:
            state[oid]["status"] = k["status"]

    monkeypatch.setattr(cod.shopify_orders, "state_map", lambda: state)
    monkeypatch.setattr(cod.shopify_orders, "set_state", _set)
    return state, calls


async def _settlement(order_id):
    async with async_session_maker() as s:
        return (await s.execute(
            select(CodSettlement).where(CodSettlement.order_id == order_id))).scalar_one_or_none()


async def test_settle_records_and_advances(owner_client, settle_state):
    _state, calls = settle_state
    r = await owner_client.post("/api/cod/settle", json={
        "reference": "P1", "payout_date": "2026-07-21", "courier": "", "lines": [{"order_id": "o1", "collected": 30.0}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["settled"], body["discrepancies"], body["skipped"]) == (1, 0, 0)
    assert body["barsy"] == {"live": False, "posted": 0}   # gated off → recorded locally only
    assert ("o1", {"status": "cod_paid"}) in calls
    row = await _settlement("o1")
    assert row and row.collected == 30.0 and row.expected == 30.0

    ov = (await owner_client.get("/api/cod")).json()
    o1 = next(o for o in ov["orders"] if o["order_id"] == "o1")
    assert o1["bucket"] == "settled" and o1["collected"] == 30.0


async def test_settle_flags_discrepancy(owner_client, settle_state):
    r = await owner_client.post("/api/cod/settle", json={
        "reference": "P", "payout_date": "", "courier": "", "lines": [{"order_id": "o1", "collected": 25.0}]})
    assert r.json()["discrepancies"] == 1
    row = await _settlement("o1")
    assert row.collected == 25.0 and row.expected == 30.0


async def test_settle_is_idempotent(owner_client, settle_state):
    body = {"reference": "P", "payout_date": "", "courier": "", "lines": [{"order_id": "o1", "collected": 30.0}]}
    assert (await owner_client.post("/api/cod/settle", json=body)).json()["settled"] == 1
    # second attempt on the same order is skipped, not double-recorded
    j = (await owner_client.post("/api/cod/settle", json=body)).json()
    assert (j["settled"], j["discrepancies"], j["skipped"]) == (0, 0, 1)


async def test_settle_prefers_order_courier_over_batch(owner_client, settle_state):
    # M1: a batch-level courier must NOT override the order's own carrier
    await owner_client.post("/api/cod/settle", json={
        "reference": "P", "payout_date": "", "courier": "speedy", "lines": [{"order_id": "o1", "collected": 30.0}]})
    row = await _settlement("o1")
    assert row.courier == "econt"  # o1's own courier, not the "speedy" batch value


async def test_settle_persists_even_if_status_flip_fails(owner_client, settle_state, monkeypatch):
    # H1: the financial row must survive a set_state failure (status flip is re-derivable)
    def _boom(*a, **k):
        raise RuntimeError("pipeline db locked")
    monkeypatch.setattr(cod.shopify_orders, "set_state", _boom)
    r = await owner_client.post("/api/cod/settle", json={
        "reference": "P", "payout_date": "", "courier": "", "lines": [{"order_id": "o1", "collected": 30.0}]})
    assert r.status_code == 200 and r.json()["settled"] == 1
    row = await _settlement("o1")
    assert row and row.collected == 30.0  # settlement durable despite the status-flip crash


async def test_overview_summary_math(owner_client, settle_state):
    ov = (await owner_client.get("/api/cod")).json()
    s = ov["summary"]
    # both delivered COD orders are awaiting payout: 30 + 20
    assert s["awaiting_amount"] == 50.0 and s["awaiting_count"] == 2
    assert s["outstanding_amount"] == 50.0 and s["settled_count"] == 0


async def test_settle_requires_staff(client, settle_state):
    r = await client.post("/api/cod/settle", json={"reference": "", "payout_date": "", "courier": "", "lines": []})
    assert r.status_code in (401, 403)


# --- storno on uncollected COD (the anonymous-paid safety net) -----------------------
async def test_storno_requires_confirm(owner_client, settle_state):
    r = await owner_client.post("/api/cod/o1/storno", json={"confirm": False})
    assert r.status_code == 400


async def test_storno_calls_connector_and_flips_returned(owner_client, settle_state, monkeypatch):
    _state, calls = settle_state
    seen = {}

    def fake_refuse(oid, reason, do_write):
        seen.update(oid=oid, reason=reason, do_write=do_write)
        return {"order_id": oid, "stornoed": True}

    monkeypatch.setattr(cod.connector, "cod_refused_to_barsy", fake_refuse, raising=False)
    r = await owner_client.post("/api/cod/o1/storno", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert seen["oid"] == "o1" and "върната" in seen["reason"]
    assert _state["o1"]["status"] == "returned"          # flipped out of the awaiting bucket
    assert ("o1", {"status": "returned"}) in calls


async def test_storno_refuses_a_settled_order(owner_client, settle_state, monkeypatch):
    _state, _calls = settle_state
    _state["o1"]["status"] = "cod_paid"                  # already reconciled/collected
    monkeypatch.setattr(cod.connector, "cod_refused_to_barsy",
                        lambda oid, reason, do_write: {"stornoed": True}, raising=False)
    r = await owner_client.post("/api/cod/o1/storno", json={"confirm": True})
    assert r.status_code == 409                          # never storno a collected/paid order


async def test_storno_unknown_order_404(owner_client, settle_state):
    r = await owner_client.post("/api/cod/nope/storno", json={"confirm": True})
    assert r.status_code == 404


async def test_storno_requires_staff(client, settle_state):
    r = await client.post("/api/cod/o1/storno", json={"confirm": True})
    assert r.status_code in (401, 403)
