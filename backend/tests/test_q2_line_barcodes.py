# -*- coding: utf-8 -*-
"""Q2: a line may carry several barcodes. Until now it held exactly one, so a product created at
intake could be given a single code and never its others — and its second code scanned as unknown
forever."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine
from app.routers import deliveries

SUP = "ТЕСТ-Q2"


async def _batch_line(barcode=""):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, depot_id=1, reference="Q2", doc_date="", source="file",
                          status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ln = DeliveryLine(batch_id=b.id, supplier_code="Q2", name="Чаша", qty=10.0, price=1.0,
                          barcode=barcode, is_new=False, match_code="", article_id=999,
                          status="pending")
        s.add(ln)
        await s.commit()
        return b.id, ln.id


# ── the placeholder trap, finally enforced ───────────────────────────────────
@pytest.mark.parametrize("junk", ["0000000000000", "000000000000", "0", "00000", "   "])
def test_filler_codes_are_recognised(junk):
    """0000000000000 EXISTS in barcodes.jsonl against MULTIPLE products — accepting it as an
    identifier ties a beep to whichever row happens to be first."""
    assert deliveries._is_placeholder(junk) is True


def test_a_real_barcode_is_not_a_placeholder():
    assert deliveries._is_placeholder("8696287052034") is False
    assert deliveries._is_placeholder("7350000000019") is False


@pytest.mark.asyncio
async def test_a_placeholder_is_refused_on_add(owner_client):
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                                json={"barcode": "0000000000000"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_a_placeholder_is_refused_on_attach(owner_client):
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/attach-barcode",
                                json={"barcode": "0000000000000"})
    assert r.status_code == 400


# ── several codes on one line ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_line_can_carry_several(owner_client):
    bid, lid = await _batch_line()
    for code in ("8696287052034", "7350000000019", "5901234123457"):
        r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                                    json={"barcode": code})
        assert r.status_code == 200, r.text
        assert r.json()["added"] is True
    got = (await owner_client.get(f"/api/deliveries/{bid}/lines/{lid}/barcodes")).json()["barcodes"]
    assert got == ["8696287052034", "7350000000019", "5901234123457"]


@pytest.mark.asyncio
async def test_the_first_code_stays_on_the_line_as_a_shim(owner_client):
    """Everything that reads DeliveryLine.barcode keeps working untouched."""
    bid, lid = await _batch_line()
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                            json={"barcode": "8696287052034"})
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                            json={"barcode": "7350000000019"})
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).barcode == "8696287052034"


@pytest.mark.asyncio
async def test_the_same_code_twice_is_not_an_error_just_a_no_op(owner_client):
    """A repeat beep should not create a duplicate, nor shout at the worker."""
    bid, lid = await _batch_line()
    body = {"barcode": "8696287052034"}
    assert (await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes", json=body)).json()["added"] is True
    second = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes", json=body)
    assert second.status_code == 200 and second.json()["added"] is False
    assert second.json()["barcodes"] == ["8696287052034"]


# ── scanning a SECOND code ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scanning_a_lines_second_barcode_finds_it(owner_client):
    """The point of the whole slice: before this, a product's other code scanned as unknown."""
    bid, lid = await _batch_line(barcode="8696287052034")
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                            json={"barcode": "7350000000019"})
    r = await owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": "7350000000019"})
    assert r.status_code == 200 and r.json()["matched"] is True
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).scanned_qty == 1.0


@pytest.mark.asyncio
async def test_scan_batch_also_sees_the_second_code(owner_client):
    bid, lid = await _batch_line(barcode="8696287052034")
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                            json={"barcode": "7350000000019"})
    r = await owner_client.post(f"/api/deliveries/{bid}/scan-batch",
                                json={"counts": {"7350000000019": 3}})
    assert r.status_code == 200 and r.json()["unknown"] == []
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).scanned_qty == 3.0


# ── removing ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_removing_the_first_code_promotes_the_next(owner_client):
    """The shim must not be left pointing at a code the line no longer has."""
    bid, lid = await _batch_line()
    for code in ("8696287052034", "7350000000019"):
        await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes", json={"barcode": code})
    r = await owner_client.delete(f"/api/deliveries/{bid}/lines/{lid}/barcodes/8696287052034")
    assert r.status_code == 200
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).barcode == "7350000000019"


@pytest.mark.asyncio
async def test_removing_the_last_code_leaves_the_line_bare(owner_client):
    bid, lid = await _batch_line()
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                            json={"barcode": "8696287052034"})
    await owner_client.delete(f"/api/deliveries/{bid}/lines/{lid}/barcodes/8696287052034")
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).barcode == ""


@pytest.mark.asyncio
async def test_barcodes_are_frozen_once_finalising(owner_client):
    bid, lid = await _batch_line()
    async with async_session_maker() as s:
        (await s.get(DeliveryBatch, bid)).status = "finalising"
        await s.commit()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/barcodes",
                                json={"barcode": "8696287052034"})
    assert r.status_code == 409
