# -*- coding: utf-8 -*-
"""H1 safety pass. The theme: a delivery must never be able to book stock twice, and must never
be able to strand with nobody knowing whether Barsy has it."""
import math

import pytest

# import it the way the app does (darling_core/__init__ does `import barsy_api as barsy`), or the
# test would patch a SECOND module object and assert against classes the app never sees
from darling_core import barsy as barsy_api

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine
from app.routers import deliveries

SUP = "ТЕСТ-H1"


async def _batch(status="draft", doc_num="", qty=3.0):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, supplier_id=None, depot_id=1, reference="H1",
                          doc_date="", source="file", status=status, doc_num=doc_num,
                          created_by="t@t")
        s.add(b)
        await s.flush()
        s.add(DeliveryLine(batch_id=b.id, supplier_code="H1-1", name="Артикул", qty=qty,
                           price=1.0, barcode="", is_new=False, match_code="14153",
                           article_id=999, status="pending"))
        await s.commit()
        return b.id


# ── A1: reads retry, writes do not ────────────────────────────────────────────
def test_read_methods_are_recognised():
    for m in ("Storeloads_getlist", "Articles_getinfo", "Suppliers_list", "Goods_search"):
        assert barsy_api._is_read_method(m), m
    for m in ("Storeloads_create", "Articles_create", "Articles_edit", "Storeloads_delete"):
        assert not barsy_api._is_read_method(m), m


def test_uncertain_error_is_a_barsy_error():
    # callers that catch BarsyError must keep catching the uncertain case
    assert issubclass(barsy_api.BarsyUncertainError, barsy_api.BarsyError)


def test_write_timeout_raises_uncertain_and_does_not_retry(monkeypatch):
    """A write that times out must NOT be re-POSTed — that is the double-booking path."""
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise TimeoutError("read timed out")

    monkeypatch.setattr(barsy_api.urllib.request, "urlopen", boom)
    monkeypatch.setattr(barsy_api, "_env", lambda: ("http://x/endpoints/json", "u", "p"))
    with pytest.raises(barsy_api.BarsyUncertainError):
        barsy_api.call("Storeloads_create", {"a": 1})
    assert calls["n"] == 1, "a write must be attempted exactly once"


def test_read_timeout_still_retries(monkeypatch):
    import time as _t
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise TimeoutError("read timed out")

    monkeypatch.setattr(barsy_api.urllib.request, "urlopen", boom)
    monkeypatch.setattr(barsy_api, "_env", lambda: ("http://x/endpoints/json", "u", "p"))
    monkeypatch.setattr(_t, "sleep", lambda *_: None)   # call() does `import time as _time`
    with pytest.raises(barsy_api.BarsyError):
        barsy_api.call("Storeloads_getlist", {})
    assert calls["n"] == 4, "a read should still exhaust its retries"


# ── A16: id extraction + fractional bound ─────────────────────────────────────
def test_storeload_id_is_not_taken_from_article_id():
    from app.barsy_ids import extract_id
    resp = {"article_id": 555, "storeload_id": 42}
    assert extract_id(resp, ("storeload_id", "id")) == 42


@pytest.mark.parametrize("scanned,expected", [
    (0, "pending"), (2, "confirmed"), (3, "confirmed"), (40, "mismatch"),
])
def test_fractional_line_is_bounded(scanned, expected):
    """A 2.5 kg line confirms at 2 or 3 — a runaway tally of 40 must NOT read as confirmed."""
    ln = DeliveryLine(batch_id=1, qty=2.5, scanned_qty=scanned, defect_qty=0.0, status="pending")
    deliveries._set_line_status(ln)
    assert ln.status == expected
    assert math.floor(2.5) == 2


# ── A8: 'finalising' freezes the batch ────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalising_batch_refuses_mutations_and_delete(owner_client):
    bid = await _batch(status="finalising", doc_num="H1-B1")
    async with async_session_maker() as s:
        ln = (await s.execute(
            deliveries.select(DeliveryLine).where(DeliveryLine.batch_id == bid))).scalars().first()
        lid = ln.id
    assert (await owner_client.post(f"/api/deliveries/{bid}/scan",
                                    json={"barcode": "X"})).status_code == 409
    assert (await owner_client.post(f"/api/deliveries/{bid}/scan-batch",
                                    json={"counts": {"X": 1}})).status_code == 409
    assert (await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect",
                                    json={"qty": 1})).status_code == 409
    # deleting mid-write would leave goods in Barsy with no local record
    assert (await owner_client.delete(f"/api/deliveries/{bid}")).status_code == 409


# ── A2: reconcile ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stuck_list_shows_finalising_batches(owner_client):
    bid = await _batch(status="finalising", doc_num="H1-STUCK")
    stuck = (await owner_client.get("/api/deliveries/stuck")).json()["stuck"]
    assert any(x["id"] == bid and x["doc_num"] == "H1-STUCK" for x in stuck)


@pytest.mark.asyncio
async def test_reconcile_adopts_when_barsy_has_the_doc_num(owner_client, monkeypatch):
    bid = await _batch(status="finalising", doc_num="H1-FOUND")
    monkeypatch.setattr(deliveries, "_find_storeload_by_doc_num", lambda *_: (True, 777))
    r = await owner_client.post(f"/api/deliveries/{bid}/reconcile")
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "adopted" and r.json()["storeload_id"] == 777
    async with async_session_maker() as s:
        b = await s.get(DeliveryBatch, bid)
        assert b.status == "finalised" and b.storeload_id == 777


@pytest.mark.asyncio
async def test_reconcile_releases_when_barsy_does_not_have_it(owner_client, monkeypatch):
    bid = await _batch(status="finalising", doc_num="H1-ABSENT")
    monkeypatch.setattr(deliveries, "_find_storeload_by_doc_num", lambda *_: (False, None))
    r = await owner_client.post(f"/api/deliveries/{bid}/reconcile")
    assert r.json()["action"] == "released"
    async with async_session_maker() as s:
        assert (await s.get(DeliveryBatch, bid)).status == "ready"


@pytest.mark.asyncio
async def test_reconcile_leaves_it_stuck_when_barsy_is_unreachable(owner_client, monkeypatch):
    """Guessing here is exactly the double-booking risk — it must stay stuck."""
    bid = await _batch(status="finalising", doc_num="H1-DOWN")

    def down(*_):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(deliveries, "_find_storeload_by_doc_num", down)
    assert (await owner_client.post(f"/api/deliveries/{bid}/reconcile")).status_code == 502
    async with async_session_maker() as s:
        assert (await s.get(DeliveryBatch, bid)).status == "finalising"


@pytest.mark.asyncio
async def test_reconcile_refuses_a_healthy_batch(owner_client):
    bid = await _batch(status="draft")
    assert (await owner_client.post(f"/api/deliveries/{bid}/reconcile")).status_code == 409


# ── A3 superseded: autofinalise is GONE (owner reversed it 2026-08-07) ───────
def test_nothing_finalises_a_delivery_automatically():
    """The scan-in-progress compromise is moot: no code path sends a storeload without a human.
    Guarding the NAME as well as the behaviour, so reintroducing it has to be deliberate."""
    assert not hasattr(deliveries, "autofinalise_if_ready")
    assert hasattr(deliveries, "refresh_delivery_readiness")
# ── the reconcile probe itself: Storeloads_getlist has no doc_num ─────────────
# Measured live 2026-08-08: list rows carry barsy_id, depot_id, doc_type_id, doc_type_name,
# status, store_load_id — and nothing else. The number is only on Storeloads_get.
def _live_shape(n=3):
    return [{"barsy_id": 100 + i, "depot_id": 1, "doc_type_id": 1, "doc_type_name": "Доставка",
             "status": 2, "store_load_id": 500 + i} for i in range(n)]


def _fake_barsy(monkeypatch, rows, details, fail_detail_for=()):
    """Fake Barsy: a thin list plus a detail call per storeload, like the real one."""
    seen = {"detail_calls": []}

    def call(method, body=None, **kw):
        if method == "Storeloads_getlist":
            return rows
        if method == "Storeloads_get":
            sid = body["store_load_id"]
            seen["detail_calls"].append(sid)
            if sid in fail_detail_for:
                raise barsy_api.BarsyError(f"HTTP 503 on Storeloads_get {sid}")
            return {"store_load": {"store_load_id": sid, "doc_num": details.get(sid, "")}}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(deliveries.barsy, "call", call)
    return seen


def test_it_finds_the_document_through_the_detail_call(monkeypatch):
    """THE regression. The list has no doc_num, so matching on the list alone never hit and the
    probe always reported the goods unbooked — on the one path that exists for the case where
    they might be."""
    seen = _fake_barsy(monkeypatch, _live_shape(3),
                       {500: "OTHER-B1", 501: "DZ-B7", 502: "OTHER-B2"})
    found, sid = deliveries._find_storeload_by_doc_num("DZ-B7", 1)
    assert found is True
    assert sid == 501
    assert seen["detail_calls"], "the number is not in the list — it has to ask for the detail"


def test_absence_is_only_reported_after_every_row_was_read(monkeypatch):
    seen = _fake_barsy(monkeypatch, _live_shape(3),
                       {500: "A", 501: "B", 502: "C"})
    found, sid = deliveries._find_storeload_by_doc_num("DZ-NOPE", 1)
    assert (found, sid) == (False, None)
    assert sorted(seen["detail_calls"]) == [500, 501, 502], "every candidate must be checked"


def test_an_unreadable_detail_raises_instead_of_reporting_absence(monkeypatch):
    """Being unable to check is not evidence of absence. `False` here would release the delivery
    and authorise a second goods-in; the caller turns the raise into a 502 that leaves it stuck."""
    _fake_barsy(monkeypatch, _live_shape(3), {500: "A", 502: "C"}, fail_detail_for=(501,))
    with pytest.raises(barsy_api.BarsyError):
        deliveries._find_storeload_by_doc_num("DZ-B7", 1)


def test_the_scan_cap_raises_rather_than_reporting_absence(monkeypatch):
    """Stopping the search is not the same fact as the document being absent."""
    rows = _live_shape(deliveries._RECONCILE_SCAN_CAP + 1)
    _fake_barsy(monkeypatch, rows, {})
    with pytest.raises(RuntimeError):
        deliveries._find_storeload_by_doc_num("DZ-B7", 1)


def test_a_list_that_is_not_a_list_raises(monkeypatch):
    """An unexpected shape must not degrade to "no rows", which reads as "Barsy holds nothing"."""
    monkeypatch.setattr(deliveries.barsy, "call", lambda *a, **k: "не мога")
    with pytest.raises(RuntimeError):
        deliveries._find_storeload_by_doc_num("DZ-B7", 1)


def test_a_doc_num_in_the_list_is_used_without_a_detail_call(monkeypatch):
    """If a future Barsy build starts returning it, do not pay for 43 round trips to learn what
    is already in hand."""
    rows = _live_shape(2)
    rows[1]["doc_num"] = "DZ-B7"
    seen = _fake_barsy(monkeypatch, rows, {500: "A"})
    found, sid = deliveries._find_storeload_by_doc_num("DZ-B7", 1)
    assert (found, sid) == (True, 501)
    assert 501 not in seen["detail_calls"]


def test_the_newest_storeload_is_checked_first(monkeypatch):
    """A delivery strands mid-finalise, so its document is among the most recent. Checking the
    oldest first would spend the whole list before finding it."""
    seen = _fake_barsy(monkeypatch, _live_shape(3), {502: "DZ-B7"})
    found, _ = deliveries._find_storeload_by_doc_num("DZ-B7", 1)
    assert found is True
    assert seen["detail_calls"][0] == 502, "highest store_load_id first"
