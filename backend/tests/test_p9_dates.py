# -*- coding: utf-8 -*-
"""P9: the three dates. Posting follows ARRIVAL, not the button-press — backdating a receipt after
a sale permanently corrupts the moving-average себестойност. And the arrival-to-document gap is a
ЗДДС чл.113 signal, surfaced as a warning rather than a block."""
import datetime as _dt

import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch
from app.routers import deliveries

SUP = "ТЕСТ-P9"


def _b(**kw):
    return DeliveryBatch(supplier=SUP, depot_id=1, reference="P9", source="file",
                         status="draft", created_by="t@t",
                         arrival_date=kw.pop("arrival_date", ""),
                         doc_date=kw.pop("doc_date", ""),
                         posting_date=kw.pop("posting_date", ""), **kw)


async def _batch(**kw):
    async with async_session_maker() as s:
        b = _b(**kw)
        s.add(b)
        await s.commit()
        return b.id


# ── reading dates ────────────────────────────────────────────────────────────
def test_a_valid_date_parses():
    assert deliveries._as_date("2026-08-08") == _dt.date(2026, 8, 8)


@pytest.mark.parametrize("junk", ["", None, "n/a", "08.08.2026", "tomorrow", "2026-13-45"])
def test_an_unreadable_date_is_None_not_today(junk):
    """A value we cannot read must NOT quietly become today — that is how a wrong posting date gets
    into the books."""
    assert deliveries._as_date(junk) is None


# ── the ЗДДС чл.113 window ───────────────────────────────────────────────────
def test_an_invoice_inside_five_days_is_quiet():
    assert deliveries._date_warnings(_b(arrival_date="2026-08-01", doc_date="2026-08-05")) == []


def test_an_invoice_later_than_five_days_warns():
    w = deliveries._date_warnings(_b(arrival_date="2026-08-01", doc_date="2026-08-10"))
    assert len(w) == 1 and "9" in w[0]


def test_an_invoice_dated_well_BEFORE_arrival_warns_too():
    """Usually a mistyped month, and worth catching before it reaches the books."""
    w = deliveries._date_warnings(_b(arrival_date="2026-08-20", doc_date="2026-08-01"))
    assert len(w) == 1 and "ПРЕДИ" in w[0]


def test_a_missing_date_produces_no_noise():
    assert deliveries._date_warnings(_b(arrival_date="", doc_date="2026-08-10")) == []
    assert deliveries._date_warnings(_b(arrival_date="2026-08-10", doc_date="")) == []


# ── setting them ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_new_delivery_records_arrival_automatically(owner_client):
    """One date typed, not three."""
    import io

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Код", "Наименование", "Количество", "Цена"])
    ws.append(["14153", "Закачалка", 2, 5.0])
    buf = io.BytesIO()
    wb.save(buf)

    r = await owner_client.post(
        "/api/deliveries",
        files={"file": ("s.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"supplier": SUP, "depot_id": "1", "reference": "P9-NEW", "doc_date": ""})
    assert r.status_code == 201, r.text
    async with async_session_maker() as s:
        b = await s.get(DeliveryBatch, r.json()["id"])
        assert b.arrival_date == _dt.datetime.now(_dt.UTC).date().isoformat()


@pytest.mark.asyncio
async def test_dates_can_be_corrected(owner_client):
    bid = await _batch(arrival_date="2026-08-01")
    r = await owner_client.post(f"/api/deliveries/{bid}/dates",
                                json={"arrival_date": "2026-08-02", "doc_date": "2026-08-03"})
    assert r.status_code == 200, r.text
    assert r.json()["arrival_date"] == "2026-08-02" and r.json()["warnings"] == []


@pytest.mark.asyncio
async def test_a_future_arrival_is_refused(owner_client):
    """Goods cannot have landed tomorrow; a mistyped year is the usual cause."""
    bid = await _batch()
    future = (_dt.datetime.now(_dt.UTC).date() + _dt.timedelta(days=3)).isoformat()
    r = await owner_client.post(f"/api/deliveries/{bid}/dates", json={"arrival_date": future})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_an_unreadable_date_is_refused_not_swallowed(owner_client):
    bid = await _batch()
    r = await owner_client.post(f"/api/deliveries/{bid}/dates", json={"doc_date": "08.08.2026"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_the_vat_window_warning_comes_back_from_the_endpoint(owner_client):
    bid = await _batch(arrival_date="2026-08-01")
    r = await owner_client.post(f"/api/deliveries/{bid}/dates", json={"doc_date": "2026-08-11"})
    assert len(r.json()["warnings"]) == 1


@pytest.mark.asyncio
async def test_dates_are_frozen_once_finalised(owner_client):
    bid = await _batch(arrival_date="2026-08-01")
    async with async_session_maker() as s:
        (await s.get(DeliveryBatch, bid)).status = "finalised"
        await s.commit()
    r = await owner_client.post(f"/api/deliveries/{bid}/dates", json={"arrival_date": "2026-08-02"})
    assert r.status_code == 409
