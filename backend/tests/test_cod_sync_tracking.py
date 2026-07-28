# -*- coding: utf-8 -*-
"""POST /api/cod/sync-tracking — the courier-poll sweep that walks every in-transit COD order and
advances the ones the courier reports delivered (delivered = the courier now HOLDS our cash, i.e. the
order moves into the 'за изплащане' bucket that /settle later reconciles).

It was untested, and it is a fan-out over live courier APIs: the interesting failures are not the
happy path but (a) one bad waybill aborting the sweep and stranding every other order, and (b) the
sweep touching orders that are not actually in transit. Couriers are mocked exactly like the other COD
tests (monkeypatch cod.courier_mod.get) — no real Speedy/Econt call.
"""
from app.routers import cod


def _state(monkeypatch, orders):
    """Install the fulfillment-state map + a recording set_state that mutates it (so a second read
    reflects the flip, as the real store does)."""
    calls: list = []

    def _set(oid, name, **k):
        calls.append((oid, name, k))
        if "status" in k and oid in orders:
            orders[oid]["status"] = k["status"]

    monkeypatch.setattr(cod.shopify_orders, "state_map", lambda: orders)
    monkeypatch.setattr(cod.shopify_orders, "set_state", _set)
    return calls


def _couriers(monkeypatch, delivered=(), boom=(), unknown=()):
    """Fake adapters. `delivered` = waybills the courier reports as delivered, `boom` = waybills whose
    track() blows up, `unknown` = courier keys that don't resolve at all. Returns the poll log."""
    polled: list = []

    class _Fake:
        def __init__(self, name):
            self.name = name

        def track(self, tracking):
            polled.append((self.name, tracking))
            if tracking in boom:
                raise RuntimeError("courier API 503")
            return {"status": "x", "status_bg": "доставена" if tracking in delivered else "в път",
                    "delivered": tracking in delivered, "last_ts": "", "events": []}

    def _get(name):
        if name in unknown:
            raise RuntimeError(f"unknown courier: {name}")   # what courier_mod.get really raises
        return _Fake(name)

    monkeypatch.setattr(cod.courier_mod, "get", _get)
    return polled


def _o(status="shipped", courier="speedy", tracking="111", cod_amount=25.0, name="#1"):
    return {"order_name": name, "status": status, "courier": courier,
            "tracking": tracking, "cod_amount": cod_amount}


# --- the happy path -----------------------------------------------------------------
async def test_advances_only_the_orders_the_courier_reports_delivered(owner_client, monkeypatch):
    orders = {"a": _o(tracking="111", name="#1"), "b": _o(tracking="222", name="#2")}
    calls = _state(monkeypatch, orders)
    _couriers(monkeypatch, delivered=("111",))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "delivered": 1}
    # the flip carries the ORDER NAME too — set_state is what /api/cod reads back, and a nameless row
    # renders as the raw gid in the reconciliation table
    assert calls == [("a", "#1", {"status": "delivered"})]
    assert orders["a"]["status"] == "delivered" and orders["b"]["status"] == "shipped"


# --- the reason this endpoint needs a test ------------------------------------------
async def test_one_failing_waybill_does_not_abort_the_whole_sweep(owner_client, monkeypatch):
    """THE regression this file exists for. The sweep fans out over live courier APIs; a single
    unknown/expired waybill or a 503 must be skipped, not propagated. Without the per-order try the
    first bad order ends the loop and every LATER order silently never advances — COD sits in the
    wrong bucket, the payout never reconciles, and nothing in the UI says why."""
    orders = {"bad": _o(tracking="666", name="#66"), "good": _o(tracking="777", name="#77")}
    calls = _state(monkeypatch, orders)
    polled = _couriers(monkeypatch, delivered=("777",), boom=("666",))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text          # the sweep itself never fails on one bad order
    assert r.json()["delivered"] == 1
    assert [t for _c, t in polled] == ["666", "777"]     # it kept going AFTER the exception
    assert calls == [("good", "#77", {"status": "delivered"})]


async def test_an_unresolvable_courier_key_is_skipped_not_fatal(owner_client, monkeypatch):
    """courier_mod.get() raises for a carrier we don't have an adapter for (an order labelled by hand,
    or a carrier removed from the registry). That lookup must sit INSIDE the per-order guard."""
    orders = {"x": _o(courier="dhl", tracking="111"), "y": _o(courier="econt", tracking="222", name="#2")}
    calls = _state(monkeypatch, orders)
    _couriers(monkeypatch, delivered=("222",), unknown=("dhl",))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] == 1
    assert calls == [("y", "#2", {"status": "delivered"})]


# --- it must not touch the wrong bucket ---------------------------------------------
async def test_only_in_transit_orders_are_polled(owner_client, monkeypatch):
    """Statuses outside 'shipped' are either not out with a courier yet (preship) or already past the
    courier (delivered/settled/returned). Re-polling a settled or returned order and flipping it back
    to 'delivered' would resurrect money we've already reconciled or written off — and polling preship
    orders burns courier API quota on waybills that don't exist yet."""
    orders = {
        "new": _o(status="new", tracking="1"), "picking": _o(status="picking", tracking="2"),
        "packed": _o(status="packed", tracking="3"),
        "awaiting": _o(status="delivered", tracking="4"),   # already collected, awaiting payout
        "settled": _o(status="cod_paid", tracking="5"),     # reconciled — must stay reconciled
        "rto": _o(status="returned", tracking="6"),         # returned — no COD to collect
    }
    calls = _state(monkeypatch, orders)
    polled = _couriers(monkeypatch, delivered=("1", "2", "3", "4", "5", "6"))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] == 0
    assert polled == [] and calls == []


async def test_non_cod_orders_are_out_of_scope(owner_client, monkeypatch):
    """This is the COD reconciliation sweep — a prepaid shipped order has no cash to collect, so it
    must never be polled here (it would also flip a prepaid order's status behind orders.py's back)."""
    orders = {"prepaid": _o(cod_amount=0.0, tracking="111"),
              "cod": _o(cod_amount=25.0, tracking="222", name="#2")}
    calls = _state(monkeypatch, orders)
    polled = _couriers(monkeypatch, delivered=("111", "222"))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert [t for _c, t in polled] == ["222"]
    assert calls == [("cod", "#2", {"status": "delivered"})]


async def test_orders_without_a_courier_or_waybill_are_skipped(owner_client, monkeypatch):
    """A manually-shipped order (no label from us) has no carrier/waybill. Passing an empty tracking
    to an adapter is a guaranteed courier-side error per sweep — cheap to guard, noisy if lost."""
    orders = {"nocourier": _o(courier="", tracking="111"),
              "notracking": _o(courier="speedy", tracking=""),
              "ok": _o(tracking="333", name="#3")}
    calls = _state(monkeypatch, orders)
    polled = _couriers(monkeypatch, delivered=("333",))
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert [t for _c, t in polled] == ["333"]
    assert calls == [("ok", "#3", {"status": "delivered"})]


async def test_each_order_is_polled_with_its_own_carrier(owner_client, monkeypatch):
    # a mixed-carrier day is the norm; sending every waybill to one adapter returns "not found" for
    # the rest and would quietly strand half the COD book
    orders = {"a": _o(courier="speedy", tracking="111"), "b": _o(courier="econt", tracking="222")}
    _state(monkeypatch, orders)
    polled = _couriers(monkeypatch)
    await owner_client.post("/api/cod/sync-tracking")
    assert set(polled) == {("speedy", "111"), ("econt", "222")}


# --- roles --------------------------------------------------------------------------
async def test_requires_login(client, monkeypatch):
    _state(monkeypatch, {"a": _o()})
    _couriers(monkeypatch)
    r = await client.post("/api/cod/sync-tracking")
    assert r.status_code in (401, 403)


async def test_packer_may_run_the_sweep(packer_client, monkeypatch):
    # deliberate: the sweep is read-mostly warehouse housekeeping (require_roles("staff", "packer")),
    # unlike /settle which is staff-only because it moves money
    orders = {"a": _o(tracking="111", name="#1")}
    calls = _state(monkeypatch, orders)
    _couriers(monkeypatch, delivered=("111",))
    r = await packer_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] == 1 and calls


async def test_a_state_write_failure_on_one_order_does_not_abort_the_sweep(owner_client, monkeypatch):
    """The guard must wrap the WHOLE per-order body, not just .track(). A pipeline-DB write failure on
    one order used to abort the entire sweep — the exact "one bad waybill strands every other order"
    failure the try/except exists to prevent (only .track() was inside it)."""
    orders = {
        "o1": {"order_name": "#1", "status": "shipped", "courier": "speedy", "tracking": "111", "cod_amount": 30.0},
        "o2": {"order_name": "#2", "status": "shipped", "courier": "speedy", "tracking": "222", "cod_amount": 20.0},
    }
    seen: list = []

    def _set(oid, name, **k):
        seen.append(oid)
        if oid == "o1":
            raise RuntimeError("pipeline db locked")   # the poison order
        orders[oid]["status"] = k.get("status", orders[oid]["status"])

    monkeypatch.setattr(cod.shopify_orders, "state_map", lambda: orders)
    monkeypatch.setattr(cod.shopify_orders, "set_state", _set)
    _couriers(monkeypatch, delivered=("111", "222"))

    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200, r.text
    assert "o2" in seen                    # the LATER order was still processed
    assert r.json()["delivered"] == 1      # only the one that actually persisted is counted


async def test_a_non_dict_track_response_is_survivable(owner_client, monkeypatch):
    """A courier adapter returning something odd must not 500 the sweep."""
    orders = {"o1": {"order_name": "#1", "status": "shipped", "courier": "speedy",
                     "tracking": "111", "cod_amount": 30.0}}
    _state(monkeypatch, orders)
    monkeypatch.setattr(cod.courier_mod, "get",
                        lambda key: type("C", (), {"track": staticmethod(lambda t: None)})())
    r = await owner_client.post("/api/cod/sync-tracking")
    assert r.status_code == 200 and r.json()["delivered"] == 0
