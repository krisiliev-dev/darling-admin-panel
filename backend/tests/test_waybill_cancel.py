# -*- coding: utf-8 -*-
"""Cancelling a courier waybill (2026-08-07).

The thing worth testing here is not the happy path. It is the difference between "the courier
said no" and "we could not ask", because those two look identical to a careless caller and mean
opposite things on the ground: a refusal means the parcel is real and moving and somebody has to
act, while an unknown means the waybill may be perfectly cancellable and we simply do not know.
Collapsing them is how a live waybill goes quiet — the courier still collects, still bills, and on
наложен платеж still takes cash for an order that no longer exists.

Everything is faked. Researched against the live APIs on 2026-08-07: every courier only voids a
waybill that has not been collected yet, so a parcel in transit legitimately answers `refused`.
"""
from types import SimpleNamespace

from sqlalchemy import select

import app.routers.orders as orders_module
from app.core import courier as courier_mod
from app.db import async_session_maker
from app.models import WaybillCancel

_ORDER = {"id": "gid://shopify/Order/2001", "name": "#2001"}


def _patch(monkeypatch, *, state=None, live=True, cancel=None):
    """An order with a waybill on it, a courier that does what the test says, and the gate on."""
    monkeypatch.setattr(orders_module, "_order_or_404", lambda oid: dict(_ORDER))
    st = {"status": "shipped", "courier": "speedy",
          "tracking": "TRK-1", "label_ref": "SHIP-9"} if state is None else state
    monkeypatch.setattr(orders_module.shopify_orders, "state_map",
                        lambda: {_ORDER["id"]: st}, raising=False)
    monkeypatch.setattr(orders_module, "get_settings",
                        lambda: SimpleNamespace(courier_live=live))
    calls = []

    class _Fake:
        def cancel(self, waybill, comment=""):
            calls.append(waybill)
            if cancel is not None:
                return cancel()
            return {"ok": True}

    monkeypatch.setattr(courier_mod, "get", lambda name: _Fake())
    return calls


async def _row(waybill="SHIP-9"):
    async with async_session_maker() as s:
        return (await s.execute(select(WaybillCancel).where(
            WaybillCancel.waybill_no == waybill))).scalar_one_or_none()


# ------------------------------------------------- the classification, in isolation
def test_a_courier_that_answered_is_a_refusal():
    """An HTTP status means something on the other end formed an opinion."""
    state, msg = orders_module._cancel_verdict(
        courier_mod.CourierError("Спиди HTTP 409: shipment already picked up"))
    assert state == "refused"
    assert "already picked up" in msg


def test_a_courier_we_could_not_reach_is_unknown():
    """No status, no answer. The waybill must be assumed live."""
    state, _ = orders_module._cancel_verdict(
        courier_mod.CourierError("Спиди мрежова грешка: timed out"))
    assert state == "unknown"


def test_a_plain_timeout_is_unknown_too():
    state, _ = orders_module._cancel_verdict(TimeoutError("read timeout"))
    assert state == "unknown"


# ------------------------------------------------------------- refusing to guess
async def test_an_order_with_no_waybill_is_refused_not_invented(owner_client, monkeypatch):
    _patch(monkeypatch, state={"status": "new"})
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.status_code == 409


async def test_a_waybill_with_no_courier_says_so(owner_client, monkeypatch):
    """We will not guess which courier holds a parcel — the wrong API call is worse than none."""
    _patch(monkeypatch, state={"status": "shipped", "label_ref": "SHIP-9", "courier": ""})
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.status_code == 409
    assert "SHIP-9" in r.json()["detail"]


# ------------------------------------------------------------------- the gates
async def test_without_confirm_it_only_returns_the_plan(owner_client, monkeypatch):
    """The plan IS the dialog: the operator sees which waybill and which courier first."""
    calls = _patch(monkeypatch)
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": False})
    body = r.json()
    assert body["state"] == "plan"
    assert body["waybill_no"] == "SHIP-9" and body["courier"] == "speedy"
    assert calls == []                                   # nothing was asked of the courier
    assert await _row() is None                           # and nothing was written


async def test_with_the_gate_off_nothing_reaches_the_courier(owner_client, monkeypatch):
    calls = _patch(monkeypatch, live=False)
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.json()["state"] == "dry_run"
    assert calls == []
    assert await _row() is None


# ---------------------------------------------------------------- the three states
async def test_a_confirmed_cancel_is_recorded_and_resolved(owner_client, monkeypatch):
    calls = _patch(monkeypatch)
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.json()["state"] == "cancelled"
    assert calls == ["SHIP-9"]                            # label_ref, not the tracking number
    row = await _row()
    assert row.state == "cancelled" and row.resolved_at is not None
    assert row.attempts == 1


async def test_a_refusal_keeps_the_couriers_own_words(owner_client, monkeypatch):
    def boom():
        raise courier_mod.CourierError("Спиди HTTP 409: пратката е взета от куриера")

    _patch(monkeypatch, cancel=boom)
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    body = r.json()
    assert body["state"] == "refused"
    assert "взета от куриера" in body["message"]           # verbatim, not paraphrased
    row = await _row()
    # A refusal is FINAL — the courier answered. Nothing left to retry.
    assert row.state == "refused" and row.resolved_at is not None


async def test_an_unreachable_courier_stays_unresolved(owner_client, monkeypatch):
    """THE one. Unknown must NOT resolve: the waybill may still be live and it has to stay on
    the worklist until a courier positively confirms."""
    def boom():
        raise courier_mod.CourierError("Спиди мрежова грешка: connection reset")

    _patch(monkeypatch, cancel=boom)
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.json()["state"] == "unknown"
    row = await _row()
    assert row.state == "unknown"
    assert row.resolved_at is None


# -------------------------------------------------------------------- idempotency
async def test_a_second_cancel_does_not_ask_the_courier_again(owner_client, monkeypatch):
    calls = _patch(monkeypatch)
    await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert calls == ["SHIP-9"]
    r = await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.json()["state"] == "cancelled" and r.json()["already"] is True
    assert calls == ["SHIP-9"]                            # still one call, not two


async def test_an_unknown_is_retryable(owner_client, monkeypatch):
    """The opposite of the above: unknown is exactly the case that SHOULD be tried again."""
    boom = {"n": 0}

    def flaky():
        boom["n"] += 1
        raise courier_mod.CourierError("Спиди мрежова грешка: timeout")

    calls = _patch(monkeypatch, cancel=flaky)
    await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert len(calls) == 2
    row = await _row()
    assert row.attempts == 2 and row.resolved_at is None


# ------------------------------------------------------------------- who may do it
async def test_a_packer_may_cancel_a_waybill(packer_client, monkeypatch):
    """Kris, 2026-08-07: packers are allowed. They are the people holding the parcel."""
    _patch(monkeypatch)
    r = await packer_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.status_code == 200, r.text


async def test_an_anonymous_caller_may_not(client, monkeypatch):
    _patch(monkeypatch)
    r = await client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------- the worklist
async def test_the_worklist_shows_only_the_unresolved(owner_client, monkeypatch):
    def boom():
        raise courier_mod.CourierError("Спиди мрежова грешка: timeout")

    _patch(monkeypatch, cancel=boom)
    await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    body = (await owner_client.get("/api/orders/waybill-cancels/unresolved")).json()
    assert body["count"] == 1
    assert body["rows"][0]["waybill_no"] == "SHIP-9"
    # Staleness, not an error count: a dead retry loop produces silence, not errors.
    assert body["oldest_hours"] is not None and body["oldest_hours"] >= 0


async def test_a_resolved_cancel_leaves_the_worklist(owner_client, monkeypatch):
    _patch(monkeypatch)
    await owner_client.post("/api/orders/2001/cancel-waybill", json={"confirm": True})
    body = (await owner_client.get("/api/orders/waybill-cancels/unresolved")).json()
    assert body["count"] == 0
    assert body["oldest_hours"] is None
