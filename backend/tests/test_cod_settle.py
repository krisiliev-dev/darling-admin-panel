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
    assert "barsy" not in body   # matched COD settles panel-side ONLY (already paid at packing — no re-post)
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
        return {"order_id": oid, "status": "ok", "storno_account_id": 900, "written": True}

    monkeypatch.setattr(cod.connector, "cod_refused_to_barsy", fake_refuse, raising=False)
    r = await owner_client.post("/api/cod/o1/storno", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert seen["oid"] == "o1" and "върната" in seen["reason"]
    assert _state["o1"]["status"] == "returned"          # flipped out of the awaiting bucket
    assert ("o1", {"status": "returned"}) in calls


# the panel's own state is GID-keyed; the connector's order log keys on the BARE numeric id
_GID = "gid://shopify/Order/9911"


def _gid_order(state, gid=_GID, **over):
    state[gid] = {"order_name": "#99", "status": "delivered", "courier": "econt",
                  "tracking": "999", "cod_amount": 40.0, **over}
    return state[gid]


async def test_storno_sends_bare_numeric_id_to_connector(owner_client, settle_state, monkeypatch):
    """HIGH: forwarding the GID verbatim made every storno a silent no-op — the connector's
    _seen_order never matched, so no Accounts_storno fired while the panel reported success."""
    state, calls = settle_state
    _gid_order(state)
    seen = {}

    def fake_refuse(oid, reason, do_write):
        seen.update(oid=oid)
        return {"status": "ok", "written": True}

    monkeypatch.setattr(cod.connector, "cod_refused_to_barsy", fake_refuse, raising=False)
    r = await owner_client.post("/api/cod/9911/storno", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert seen["oid"] == "9911"                          # bare numeric — NOT the GID
    assert state[_GID]["status"] == "returned"            # panel state still written under its GID key
    assert (_GID, {"status": "returned"}) in calls


@pytest.mark.parametrize("result, expect_status", [
    ({"order_id": "9911", "dry_run": True}, "dry_run"),            # COD_BARSY_POST_LIVE off
    ({"status": "refused", "reason": "no_logged_order"}, "refused"),
    ({"status": "needs_review", "reason": "no_unp"}, "needs_review"),
])
async def test_storno_without_a_real_reversal_does_not_flip_returned(
        owner_client, settle_state, monkeypatch, result, expect_status):
    """MED: flipping to 'returned' regardless of the connector's answer dropped uncollected COD out
    of the panel while Barsy still held it as a PAID sale — defeating the whole backstop."""
    state, calls = settle_state
    monkeypatch.setattr(cod.connector, "cod_refused_to_barsy",
                        lambda oid, reason, do_write: result, raising=False)
    r = await owner_client.post("/api/cod/o1/storno", json={"confirm": True})
    assert r.status_code == 200, r.text                   # explicit non-ok, not an exception
    b = r.json()
    assert b["ok"] is False and b["status"] == expect_status and b["message"]
    assert b["result"] == result                          # the raw connector answer reaches the UI
    assert state["o1"]["status"] == "delivered"           # stays in the awaiting bucket
    assert not any(k.get("status") == "returned" for _oid, k in calls)


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


async def test_settle_never_posts_payment_to_barsy(owner_client, settle_state, monkeypatch):
    # anonymous-paid model: the sale is PAID at packing, so settle must NEVER re-post a payment
    # (would double-pay). Blow up if the connector payment call is reached.
    def _explode(*a, **k):
        raise AssertionError("settle must not post a COD payment to Barsy under the paid model")
    monkeypatch.setattr(cod.connector, "cod_payment_to_barsy", _explode, raising=False)
    r = await owner_client.post("/api/cod/settle", json={
        "reference": "P", "payout_date": "", "courier": "", "lines": [{"order_id": "o1", "collected": 30.0}]})
    assert r.status_code == 200 and r.json()["settled"] == 1


# --- Barsy method-6 "expected COD" pull (authoritative source) -----------------------
async def test_barsy_booked_lists_and_flags_uncollected(owner_client, settle_state, monkeypatch):
    monkeypatch.setattr(cod.connector, "list_cod_accounts", lambda: [
        {"order_id": "o1", "order_ref": "#1", "amount": 30.0, "stornoed": False, "tracking_number": None},
        {"order_id": "o3", "order_ref": "#3", "amount": 9.0, "stornoed": True},   # reversed → excluded
    ], raising=False)
    r = await owner_client.get("/api/cod/barsy-booked")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["count"] == 1 and b["total"] == 30.0        # the stornoed account is excluded
    assert b["uncollected"] == 1                          # o1 booked-paid but not settled/returned
    assert b["accounts"][0]["order_id"] == "o1" and b["accounts"][0]["uncollected"] is True


async def test_barsy_booked_joins_gid_state_to_numeric_connector_ids(
        owner_client, settle_state, monkeypatch):
    """MED: list_cod_accounts returns NUMERIC order ids while the panel's state map and settlement
    rows are GID-keyed — un-normalised, both joins always missed and EVERY collected/settled COD was
    flagged uncollected, making the report pure noise."""
    state, _calls = settle_state
    state.pop("o1"); state.pop("o2")            # only the GID-keyed orders matter here
    _gid_order(state)                                                 # 9911 — has a settlement row
    _gid_order(state, "gid://shopify/Order/8822", order_name="#88", status="returned")   # 8822 — RTO
    async with async_session_maker() as s:
        s.add(CodSettlement(order_id=_GID, order_name="#99", expected=40.0, collected=40.0))
        await s.commit()
    monkeypatch.setattr(cod.connector, "list_cod_accounts", lambda: [
        {"order_id": "9911", "order_ref": "#99", "amount": 40.0},
        {"order_id": "8822", "order_ref": "#88", "amount": 12.0},
    ], raising=False)
    b = (await owner_client.get("/api/cod/barsy-booked")).json()
    by = {a["order_id"]: a for a in b["accounts"]}
    assert by["9911"]["settled"] is True        # GID settlement row ↔ numeric Barsy id
    assert by["8822"]["bucket"] == "rto"        # GID state map ↔ numeric Barsy id
    assert b["uncollected"] == 0                # neither is a storno candidate


async def test_barsy_booked_missing_connector_fn_501(owner_client, settle_state, monkeypatch):
    monkeypatch.delattr(cod.connector, "list_cod_accounts", raising=False)
    r = await owner_client.get("/api/cod/barsy-booked")
    assert r.status_code == 501
