# -*- coding: utf-8 -*-
"""H2a scan integrity: a tally you can take back, increments that survive two phones, and a code
that appears on two rows landing on both."""
import asyncio

import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine

SUP = "ТЕСТ-H2"
EAN = "8696287052034"      # real supplier EAN -> our code 14153 (barcodes.jsonl)
OUR = "14153"


async def _batch(lines):
    """lines: list of dicts with qty / barcode / match_code / scanned_qty / defect_qty."""
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, supplier_id=None, depot_id=1, reference="H2",
                          doc_date="", source="file", status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ids = []
        for spec in lines:
            ln = DeliveryLine(
                batch_id=b.id, supplier_code=spec.get("sc", "H2"), name=spec.get("name", "Артикул"),
                qty=spec.get("qty", 3.0), price=1.0, barcode=spec.get("barcode", ""),
                is_new=False, match_code=spec.get("match_code", ""), article_id=999,
                scanned_qty=spec.get("scanned_qty", 0.0), defect_qty=spec.get("defect_qty", 0.0),
                status="pending")
            s.add(ln)
            await s.flush()
            ids.append(ln.id)
        await s.commit()
        return b.id, ids


async def _line(lid):
    async with async_session_maker() as s:
        return await s.get(DeliveryLine, lid)


# ── A4: taking a scan back ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_double_beep_can_be_undone(owner_client):
    bid, (lid,) = await _batch([{"qty": 3.0, "scanned_qty": 4.0}])
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/scan-adjust", json={"delta": -1})
    assert r.status_code == 200, r.text
    assert r.json()["line"]["scanned_qty"] == 3.0
    assert r.json()["line"]["status"] == "confirmed"      # 4 was a mismatch, 3 is right


@pytest.mark.asyncio
async def test_reset_clears_the_tally(owner_client):
    bid, (lid,) = await _batch([{"qty": 5.0, "scanned_qty": 5.0}])
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/scan-adjust", json={"reset": True})
    assert r.json()["line"]["scanned_qty"] == 0.0 and r.json()["line"]["status"] == "pending"


@pytest.mark.asyncio
async def test_adjust_never_goes_negative(owner_client):
    bid, (lid,) = await _batch([{"qty": 3.0, "scanned_qty": 1.0}])
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/scan-adjust", json={"delta": -5})
    assert r.json()["line"]["scanned_qty"] == 0.0


@pytest.mark.asyncio
async def test_scan_then_inspect_can_reach_green(owner_client):
    """The real workflow that was previously a dead end: scan the whole box, then find 2 broken."""
    bid, (lid,) = await _batch([{"qty": 10.0, "scanned_qty": 10.0}])
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 2})
    assert (await _line(lid)).status == "mismatch"        # 10 scanned vs 8 receivable
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/scan-adjust", json={"delta": -2})
    assert r.json()["line"]["status"] == "confirmed"      # 8 scanned, 8 receivable


@pytest.mark.asyncio
async def test_adjust_refuses_a_finalising_batch(owner_client):
    bid, (lid,) = await _batch([{"qty": 3.0, "scanned_qty": 1.0}])
    async with async_session_maker() as s:
        (await s.get(DeliveryBatch, bid)).status = "finalising"
        await s.commit()
    assert (await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/scan-adjust",
                                    json={"delta": -1})).status_code == 409


# ── A5: the same code on two rows ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tally_spills_into_the_second_line_with_the_same_code(owner_client):
    """A supplier listing one article on two rows (two lots, two prices) is routine. Every beep
    used to land on row 1, over-scanning it while row 2 stayed at 0 — both then blocked finalise."""
    bid, (a, b_) = await _batch([
        {"qty": 3.0, "barcode": "SAME-1"},
        {"qty": 2.0, "barcode": "SAME-1"},
    ])
    r = await owner_client.post(f"/api/deliveries/{bid}/scan-batch", json={"counts": {"SAME-1": 5}})
    assert r.status_code == 200, r.text
    la, lb = await _line(a), await _line(b_)
    assert (la.scanned_qty, lb.scanned_qty) == (3.0, 2.0)
    assert la.status == "confirmed" and lb.status == "confirmed"


@pytest.mark.asyncio
async def test_single_scan_prefers_the_line_with_room(owner_client):
    bid, (full, empty) = await _batch([
        {"qty": 1.0, "barcode": "SAME-2", "scanned_qty": 1.0},
        {"qty": 1.0, "barcode": "SAME-2"},
    ])
    await owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": "SAME-2"})
    assert (await _line(full)).scanned_qty == 1.0        # untouched, it was already full
    assert (await _line(empty)).scanned_qty == 1.0       # the beep went where there was room


@pytest.mark.asyncio
async def test_surplus_lands_on_the_last_line_not_nowhere(owner_client):
    """Over-delivery must still be visible as a mismatch, never silently dropped."""
    bid, (a, b_) = await _batch([
        {"qty": 1.0, "barcode": "SAME-3"},
        {"qty": 1.0, "barcode": "SAME-3"},
    ])
    await owner_client.post(f"/api/deliveries/{bid}/scan-batch", json={"counts": {"SAME-3": 5}})
    la, lb = await _line(a), await _line(b_)
    assert la.scanned_qty + lb.scanned_qty == 5.0
    assert lb.status == "mismatch"


@pytest.mark.asyncio
async def test_ean_still_resolves_through_the_catalogue(owner_client):
    bid, (lid,) = await _batch([{"qty": 2.0, "match_code": OUR}])
    r = await owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": EAN})
    assert r.json()["matched"] is True
    assert (await _line(lid)).scanned_qty == 1.0


# ── A9: concurrent beeps must not be lost ─────────────────────────────────────
@pytest.mark.asyncio
async def test_concurrent_scans_do_not_lose_beeps(owner_client):
    """Two phones on one pallet. The old `ln.scanned_qty += 1` was an unlocked read-modify-write:
    both read 3, both wrote 4, and a beep vanished."""
    bid, (lid,) = await _batch([{"qty": 10.0, "barcode": "CONC-1"}])
    await asyncio.gather(*[
        owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": "CONC-1"})
        for _ in range(6)
    ])
    assert (await _line(lid)).scanned_qty == 6.0


@pytest.mark.asyncio
async def test_defect_rejects_a_stale_expected_value(owner_client):
    """Two workers each marking one broken unit would otherwise land on 1 and lose the other."""
    bid, (lid,) = await _batch([{"qty": 5.0}])
    await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect", json={"qty": 1})
    stale = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect",
                                    json={"qty": 1, "expected_defect_qty": 0})
    assert stale.status_code == 409
    fresh = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/defect",
                                    json={"qty": 2, "expected_defect_qty": 1})
    assert fresh.status_code == 200 and fresh.json()["line"]["defect_qty"] == 2.0
