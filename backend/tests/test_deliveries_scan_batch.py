# -*- coding: utf-8 -*-
"""Camera batch scan (/scan-batch): one tally call applies many codes atomically.

The camera scanner locks several barcodes in one view — including several physical items of
the SAME code as separate bounding boxes — and commits them as counts. These tests mirror the
single-scan suite's real anchors (АНИС 05203 → our 14153, EAN 8696287052034) so matching runs
against the bundled supplier_codes.jsonl + barcodes.jsonl, not mocks."""

import io

import openpyxl
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch

SUP = "АНИС"
EXIST_REF = "05203"
EXIST_EAN = "8696287052034"


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(content):
    return {"file": ("dostavka.xlsx", content,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


async def _create(client):
    sheet = _xlsx([
        ["Код", "Наименование", "Количество", "Цена"],
        [EXIST_REF, "Закачалка", 3, "2,50"],                 # existing → line barcode = EXIST_EAN
        ["NOVO-1", "Нов непознат артикул хикс", 5, 4.20],     # new, no barcode yet
    ])
    r = await client.post("/api/deliveries",
                          data={"supplier": SUP, "depot_id": 1, "reference": "INV-CAM"},
                          files=_upload(sheet))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_batch_applies_counts_and_confirms(owner_client):
    b = await _create(owner_client)
    r = await owner_client.post(f"/api/deliveries/{b['id']}/scan-batch",
                                json={"counts": {EXIST_EAN: 3}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unknown"] == []
    assert len(body["applied"]) == 1
    line = body["applied"][0]["line"]
    assert line["scanned_qty"] == 3 and line["status"] == "confirmed"
    assert body["summary"]["confirmed"] == 1        # summary reflects the post-apply state


@pytest.mark.asyncio
async def test_batch_overcount_flags_mismatch(owner_client):
    b = await _create(owner_client)
    r = (await owner_client.post(f"/api/deliveries/{b['id']}/scan-batch",
                                 json={"counts": {EXIST_EAN: 5}})).json()
    assert r["applied"][0]["line"]["status"] == "mismatch"


@pytest.mark.asyncio
async def test_batch_mixed_known_and_unknown(owner_client):
    b = await _create(owner_client)
    r = (await owner_client.post(
        f"/api/deliveries/{b['id']}/scan-batch",
        json={"counts": {EXIST_EAN: 2, "0000000000000": 1}})).json()
    # the known code is applied, the unknown comes back for the tie dialog — NOT counted
    assert [a["barcode"] for a in r["applied"]] == [EXIST_EAN]
    assert r["unknown"] == ["0000000000000"]


@pytest.mark.asyncio
async def test_batch_accumulates_with_single_scans(owner_client):
    # a camera commit and a HID single scan land on the same counters
    b = await _create(owner_client)
    await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})
    r = (await owner_client.post(f"/api/deliveries/{b['id']}/scan-batch",
                                 json={"counts": {EXIST_EAN: 2}})).json()
    assert r["applied"][0]["line"]["scanned_qty"] == 3
    assert r["applied"][0]["line"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_batch_rejects_bad_counts(owner_client):
    b = await _create(owner_client)
    for bad in ({}, {EXIST_EAN: 0}, {EXIST_EAN: 501}, {"   ": 2}):
        r = await owner_client.post(f"/api/deliveries/{b['id']}/scan-batch",
                                    json={"counts": bad})
        assert r.status_code == 400, (bad, r.text)


@pytest.mark.asyncio
async def test_batch_refused_on_finalised(owner_client):
    b = await _create(owner_client)
    async with async_session_maker() as s:
        batch = await s.get(DeliveryBatch, b["id"])
        batch.status = "finalised"
        await s.commit()
    r = await owner_client.post(f"/api/deliveries/{b['id']}/scan-batch",
                                json={"counts": {EXIST_EAN: 1}})
    assert r.status_code == 409
