# -*- coding: utf-8 -*-
"""D9: a short delivery must book what ARRIVED. Booking the document quantity was creating phantom
stock in the exact step meant to record reality."""
import pytest

from app.models import DeliveryLine
from app.routers import deliveries


def _line(qty=10.0, scanned=0.0, defect=0.0):
    return DeliveryLine(batch_id=1, qty=qty, scanned_qty=scanned, defect_qty=defect,
                        price=1.0, status="pending")


# ── what gets booked ─────────────────────────────────────────────────────────
def test_a_counted_line_books_the_count_not_the_document():
    """The bug: the supplier billed 10, 8 arrived, Barsy was told 10."""
    assert deliveries._booked_qty(_line(qty=10.0, scanned=8.0)) == 8.0
    assert deliveries._shortfall(_line(qty=10.0, scanned=8.0)) == 2.0


def test_an_unscanned_line_still_trusts_the_document():
    """Nobody counted it, so the paper is all we have — but that is now a fallback, not an override."""
    assert deliveries._booked_qty(_line(qty=10.0, scanned=0.0)) == 10.0
    assert deliveries._shortfall(_line(qty=10.0, scanned=0.0)) == 0.0


def test_defects_never_book_and_are_not_a_shortfall():
    """A defective unit ARRIVED — it is a claim, not something the supplier failed to send."""
    ln = _line(qty=10.0, scanned=10.0, defect=2.0)
    assert deliveries._booked_qty(ln) == 8.0
    assert deliveries._shortfall(ln) == 0.0


def test_short_and_defective_together():
    """9 of 10 turned up and 2 of those are broken: book 7, and only the missing 1 is a shortfall."""
    ln = _line(qty=10.0, scanned=9.0, defect=2.0)
    assert deliveries._booked_qty(ln) == 7.0
    assert deliveries._shortfall(ln) == 1.0


def test_an_over_delivery_books_the_surplus_and_is_not_short():
    ln = _line(qty=10.0, scanned=12.0)
    assert deliveries._booked_qty(ln) == 12.0
    assert deliveries._shortfall(ln) == 0.0


def test_nothing_arrived_books_nothing_and_never_goes_negative():
    ln = _line(qty=5.0, scanned=1.0, defect=3.0)
    assert deliveries._booked_qty(ln) == 0.0        # 1 counted, 3 of them defective -> clamp at 0
    assert deliveries._shortfall(ln) == 2.0


# ── the tolerance: refuse once, accept with a reason ─────────────────────────
@pytest.mark.asyncio
async def test_short_finalise_is_refused_until_a_reason_is_given(owner_client, barsy_recorder, monkeypatch):
    from types import SimpleNamespace

    import app.routers.deliveries as dr
    monkeypatch.setattr(dr, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1,
                                                barsy_currency_id=1, barsy_price_mode=1))
    barsy_recorder.set_return("Storeloads_create", {"storeload_id": 9500})

    from app.db import async_session_maker
    from app.models import DeliveryBatch
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier="ТЕСТ-D9", depot_id=1, reference="D9", doc_date="",
                          source="file", status="ready", created_by="t@t")
        s.add(b)
        await s.flush()
        s.add(DeliveryLine(batch_id=b.id, name="Артикул", qty=10.0, price=1.0, is_new=False,
                           match_code="14153", article_id=999, scanned_qty=8.0,
                           status="mismatch"))
        await s.commit()
        bid = b.id

    # blocked, with the numbers spelled out — and nothing sent
    r = await owner_client.post(f"/api/deliveries/{bid}/finalise",
                                json={"confirm": True, "force": True})
    assert r.status_code == 409
    assert "2" in r.json()["detail"]
    assert barsy_recorder.operations.count("Storeloads_create") == 0

    # a reason lets it through, and only the 8 that arrived are booked
    r = await owner_client.post(f"/api/deliveries/{bid}/finalise",
                                json={"confirm": True, "force": True,
                                      "shortfall_reason": "две липсват, шофьорът потвърди"})
    assert r.status_code == 200, r.text
    sent = r.json()["storeload_payload"]["articles"]
    assert sent[0]["amount"] == "8.0"

    async with async_session_maker() as s:
        b = await s.get(DeliveryBatch, bid)
        assert b.shortfall_reason.startswith("две липсват")
        ln = (await s.execute(
            dr.select(DeliveryLine).where(DeliveryLine.batch_id == bid))).scalars().first()
        assert ln.short_qty == 2.0        # frozen onto the record


@pytest.mark.asyncio
async def test_a_complete_delivery_needs_no_reason(owner_client, barsy_recorder, monkeypatch):
    from types import SimpleNamespace

    import app.routers.deliveries as dr
    monkeypatch.setattr(dr, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1,
                                                barsy_currency_id=1, barsy_price_mode=1))
    barsy_recorder.set_return("Storeloads_create", {"storeload_id": 9501})

    from app.db import async_session_maker
    from app.models import DeliveryBatch
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier="ТЕСТ-D9", depot_id=1, reference="D9-OK", doc_date="",
                          source="file", status="ready", created_by="t@t")
        s.add(b)
        await s.flush()
        s.add(DeliveryLine(batch_id=b.id, name="Артикул", qty=4.0, price=1.0, is_new=False,
                           match_code="14153", article_id=999, scanned_qty=4.0,
                           status="confirmed"))
        await s.commit()
        bid = b.id

    r = await owner_client.post(f"/api/deliveries/{bid}/finalise", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["storeload_payload"]["articles"][0]["amount"] == "4.0"
