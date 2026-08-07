# -*- coding: utf-8 -*-
"""Receive a delivery from a Заявка: list sent заявки, build a batch from their lines, link it,
and flip the заявка to 'received' when the delivery finalises. Anchor: АНИС 05203 → our 14153."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, Zayavka, ZayavkaLine
from app.routers import deliveries

SUP = "АНИС"
EXIST_REF = "05203"
EXIST_CODE = "14153"
EXIST_EAN = "8696287052034"


async def _make_zayavka(status="sent", lines=True):
    async with async_session_maker() as s:
        z = Zayavka(supplier=SUP, supplier_id=None, status=status, created_by="t@t")
        s.add(z)
        await s.flush()
        if lines:
            s.add(ZayavkaLine(zayavka_id=z.id, code="", supplier_code=EXIST_REF,
                              name="Закачалка", qty=4, delivery_price=2.5, barcode=EXIST_EAN))
            s.add(ZayavkaLine(zayavka_id=z.id, code="", supplier_code="NOVO-9",
                              name="Нов артикул от заявка", qty=2, delivery_price=3.0))
        await s.commit()
        return z.id


@pytest.mark.asyncio
async def test_open_list_shows_sent_zayavki(owner_client):
    zid = await _make_zayavka()
    lst = (await owner_client.get("/api/deliveries/zayavki-open")).json()["zayavki"]
    row = next(x for x in lst if x["id"] == zid)
    assert row["supplier"] == SUP and row["lines"] == 2


@pytest.mark.asyncio
async def test_receive_from_zayavka_builds_a_linked_batch(owner_client):
    zid = await _make_zayavka()
    r = await owner_client.post(f"/api/deliveries/from-zayavka/{zid}")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["total"] == 2 and b["new"] == 1 and b["existing"] == 1
    exist = next(ln for ln in b["lines"] if not ln["is_new"])
    assert exist["match_code"] == EXIST_CODE and exist["qty"] == 4
    assert exist["barcode"] == EXIST_EAN            # barcode carried from the заявка line
    # the batch is linked back to the заявка; the заявка stays 'sent' until finalise
    async with async_session_maker() as s:
        batch = await s.get(DeliveryBatch, b["id"])
        assert batch.zayavka_id == zid and batch.source == "zayavka"
        z = await s.get(Zayavka, zid)
        assert z.status == "sent"


@pytest.mark.asyncio
async def test_finalise_flips_the_zayavka_to_received(owner_client):
    # exercise the flip helper directly — the full finalise needs a live Barsy write, but the
    # заявка-status effect is exactly _flip_zayavka_received, and that is what we assert.
    zid = await _make_zayavka()
    r = await owner_client.post(f"/api/deliveries/from-zayavka/{zid}")
    bid = r.json()["id"]
    async with async_session_maker() as s:
        batch = await s.get(DeliveryBatch, bid)
        await deliveries._flip_zayavka_received(s, batch)
        await s.commit()
        z = await s.get(Zayavka, zid)
        assert z.status == "received"


@pytest.mark.asyncio
async def test_refuse_received_or_empty_zayavka(owner_client):
    done = await _make_zayavka(status="received")
    assert (await owner_client.post(f"/api/deliveries/from-zayavka/{done}")).status_code == 409
    empty = await _make_zayavka(lines=False)
    assert (await owner_client.post(f"/api/deliveries/from-zayavka/{empty}")).status_code == 400
    assert (await owner_client.post("/api/deliveries/from-zayavka/999999")).status_code == 404
