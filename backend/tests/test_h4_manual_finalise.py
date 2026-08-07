# -*- coding: utf-8 -*-
"""H4 — finalising is a human act. Creating the last product must leave the delivery READY and
waiting, never sent. Owner's reversal, 2026-08-07: an automatic send left the operator unsure
whether it had happened at all."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine
from app.routers import deliveries

SUP = "ТЕСТ-H4"


async def _batch(article_id=None, is_new=True, status="awaiting_products"):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, supplier_id=None, depot_id=1, reference="H4",
                          doc_date="", source="file", status=status, created_by="t@t")
        s.add(b)
        await s.flush()
        s.add(DeliveryLine(batch_id=b.id, supplier_code="H4", name="Артикул", qty=2.0, price=1.0,
                           barcode="", is_new=is_new, match_code="", article_id=article_id,
                           status="pending"))
        await s.commit()
        return b.id


@pytest.mark.asyncio
async def test_creating_the_last_product_marks_ready_but_sends_nothing(owner_client, monkeypatch):
    """The exact moment that used to fire a storeload on its own."""
    sent = {"n": 0}

    def tripwire(*a, **k):
        sent["n"] += 1
        raise AssertionError("a storeload was sent without a human pressing Финализирай")

    monkeypatch.setattr(deliveries.barsy, "call", tripwire)
    bid = await _batch(article_id=999, is_new=True)      # product now exists in Barsy
    async with async_session_maker() as s:
        out = await deliveries.refresh_delivery_readiness(s, bid)
    assert out["ready"] is True and out["sent"] is False
    assert sent["n"] == 0
    async with async_session_maker() as s:
        b = await s.get(DeliveryBatch, bid)
        assert b.status == "ready"                        # ready, NOT finalised
        assert b.storeload_id is None


@pytest.mark.asyncio
async def test_a_delivery_still_missing_products_is_not_ready(owner_client):
    bid = await _batch(article_id=None, is_new=True)
    async with async_session_maker() as s:
        out = await deliveries.refresh_delivery_readiness(s, bid)
    assert out["ready"] is False and out["awaiting_products"] == 1
    async with async_session_maker() as s:
        assert (await s.get(DeliveryBatch, bid)).status == "awaiting_products"


@pytest.mark.asyncio
async def test_the_badge_stops_lying_once_products_exist(owner_client):
    """A14: _recompute_status ran once at creation and never again, so a delivery whose products
    had all been created still displayed „чакат нови продукти" forever."""
    bid = await _batch(article_id=None, is_new=True)
    async with async_session_maker() as s:
        ln = (await s.execute(
            deliveries.select(DeliveryLine).where(DeliveryLine.batch_id == bid))).scalars().first()
        ln.article_id = 4242                              # what intake writes back on push
        await s.commit()
        await deliveries.refresh_delivery_readiness(s, bid)
    async with async_session_maker() as s:
        assert (await s.get(DeliveryBatch, bid)).status == "ready"


@pytest.mark.asyncio
async def test_a_finalised_delivery_is_left_alone(owner_client):
    bid = await _batch(article_id=999, is_new=False, status="finalised")
    async with async_session_maker() as s:
        out = await deliveries.refresh_delivery_readiness(s, bid)
    assert out.get("already") is True and out["sent"] is False


@pytest.mark.asyncio
async def test_finalise_still_refuses_while_products_are_missing(owner_client):
    """The block the owner asked for, enforced server-side as well as on the button."""
    bid = await _batch(article_id=None, is_new=True)
    r = await owner_client.post(f"/api/deliveries/{bid}/finalise", json={"confirm": True})
    assert r.status_code == 409
