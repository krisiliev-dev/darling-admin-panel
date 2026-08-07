# -*- coding: utf-8 -*-
"""Defective-items flow (D3): a worker sets aside defective units, they are NOT received into stock
(received = qty − defect_qty), and a SupplierClaim tracks what the supplier owes. Tests hit the real
endpoints; each builds its own batch and filters claims by batch_id so a shared DB can't cross-talk."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine

SUP = "ТЕСТ-ДЕФЕКТ"


async def _make_batch(supplier=SUP, qty=3.0, price=2.0):
    """A draft batch with one existing (matched) line of `qty` units. Returns (batch_id, line_id)."""
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=supplier, supplier_id=None, depot_id=1, reference="T-D3",
                          doc_date="", source="file", status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ln = DeliveryLine(batch_id=b.id, supplier_code="SC-1", name="Артикул с дефект",
                          qty=qty, price=price, barcode="", is_new=False, match_code="14153",
                          article_id=999, status="pending")
        s.add(ln)
        await s.commit()
        return b.id, ln.id


async def _claims_for_batch(client, batch_id, state=""):
    q = f"/api/deliveries/claims?state={state}" if state else "/api/deliveries/claims"
    all_claims = (await client.get(q)).json()["claims"]
    return [c for c in all_claims if c["batch_id"] == batch_id]


@pytest.mark.asyncio
async def test_defect_reduces_received_and_opens_claim(owner_client):
    bid, lid = await _make_batch(qty=3.0)
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 1, "note": "счупено"})
    assert r.status_code == 200, r.text
    line = r.json()["line"]
    assert line["defect_qty"] == 1.0 and line["received"] == 2.0    # 3 delivered − 1 defective
    claims = await _claims_for_batch(owner_client, bid)
    assert len(claims) == 1
    c = claims[0]
    assert c["supplier"] == SUP and c["qty"] == 1.0 and c["status"] == "pending" and c["note"] == "счупено"


@pytest.mark.asyncio
async def test_open_banner_counts_pending_for_supplier(owner_client):
    bid, lid = await _make_batch()
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 1})
    resp = (await owner_client.get(f"/api/deliveries/claims-open?supplier={SUP}")).json()
    assert resp["count"] >= 1
    assert any(c["batch_id"] == bid for c in resp["claims"])


@pytest.mark.asyncio
async def test_resolve_moves_out_of_open(owner_client):
    bid, lid = await _make_batch()
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 2})
    claim_id = (await _claims_for_batch(owner_client, bid))[0]["id"]
    r = await owner_client.post(f"/api/deliveries/claims/{claim_id}/resolve",
                                json={"status": "replacement", "note": "ще донесе на следваща доставка"})
    assert r.status_code == 200, r.text
    assert r.json()["claim"]["status"] == "replacement" and r.json()["claim"]["resolved_at"]
    assert await _claims_for_batch(owner_client, bid, state="open") == []
    assert len(await _claims_for_batch(owner_client, bid, state="resolved")) == 1


@pytest.mark.asyncio
async def test_defect_zero_clears_line_and_claim(owner_client):
    bid, lid = await _make_batch(qty=4.0)
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 2})
    assert len(await _claims_for_batch(owner_client, bid)) == 1
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 0})
    assert r.status_code == 200, r.text
    assert r.json()["line"]["received"] == 4.0 and r.json()["line"]["defect_qty"] == 0.0
    assert await _claims_for_batch(owner_client, bid) == []       # the phantom claim is gone


@pytest.mark.asyncio
async def test_rejects_overcount_and_finalised(owner_client):
    bid, lid = await _make_batch(qty=3.0)
    over = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 99})
    assert over.status_code == 400
    # a finalised delivery is frozen
    async with async_session_maker() as s:
        b = await s.get(DeliveryBatch, bid)
        b.status = "finalised"
        await s.commit()
    frozen = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 1})
    assert frozen.status_code == 409
    # unknown batch / line
    assert (await owner_client.post(f"/api/deliveries/999999/lines/{lid}/defect", json={"qty": 1})).status_code == 404
