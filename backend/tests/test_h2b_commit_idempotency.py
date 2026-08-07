# -*- coding: utf-8 -*-
"""H2b: a retried camera commit must not count twice. The scanner sends the same commit_id on
every retry of one tally, so a reply lost to warehouse wifi cannot become doubled stock."""
import asyncio

import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine

SUP = "ТЕСТ-H2B"


async def _batch(qty=10.0, barcode="H2B-1"):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, supplier_id=None, depot_id=1, reference="H2B",
                          doc_date="", source="file", status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ln = DeliveryLine(batch_id=b.id, supplier_code="H2B", name="Артикул", qty=qty, price=1.0,
                          barcode=barcode, is_new=False, match_code="", article_id=999,
                          status="pending")
        s.add(ln)
        await s.commit()
        return b.id, ln.id


async def _scanned(lid):
    async with async_session_maker() as s:
        return (await s.get(DeliveryLine, lid)).scanned_qty


@pytest.mark.asyncio
async def test_same_commit_id_applies_once(owner_client):
    """The lost-reply retry: the worker presses Добави again with the SAME tally."""
    bid, lid = await _batch()
    body = {"counts": {"H2B-1": 3}, "commit_id": "fixed-commit-1"}
    first = await owner_client.post(f"/api/deliveries/{bid}/scan-batch", json=body)
    assert first.status_code == 200, first.text
    assert first.json().get("duplicate") is not True
    assert await _scanned(lid) == 3.0

    second = await owner_client.post(f"/api/deliveries/{bid}/scan-batch", json=body)
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["applied"] == []
    assert await _scanned(lid) == 3.0, "the retry must not book the box a second time"


@pytest.mark.asyncio
async def test_a_new_tally_gets_a_new_id_and_does_apply(owner_client):
    """De-duplication must not block the NEXT box — that would be worse than the bug."""
    bid, lid = await _batch()
    await owner_client.post(f"/api/deliveries/{bid}/scan-batch",
                            json={"counts": {"H2B-1": 2}, "commit_id": "box-a"})
    await owner_client.post(f"/api/deliveries/{bid}/scan-batch",
                            json={"counts": {"H2B-1": 2}, "commit_id": "box-b"})
    assert await _scanned(lid) == 4.0


@pytest.mark.asyncio
async def test_concurrent_identical_retries_apply_once(owner_client):
    """Two retries in flight at once — the unique index, not a check-then-act, is the guarantee."""
    bid, lid = await _batch()
    body = {"counts": {"H2B-1": 5}, "commit_id": "race-1"}
    rs = await asyncio.gather(*[
        owner_client.post(f"/api/deliveries/{bid}/scan-batch", json=body) for _ in range(4)
    ], return_exceptions=True)
    oks = [r for r in rs if not isinstance(r, Exception) and r.status_code == 200]
    assert len(oks) == 4
    assert sum(1 for r in oks if r.json().get("duplicate") is not True) == 1
    assert await _scanned(lid) == 5.0


@pytest.mark.asyncio
async def test_commit_id_is_optional(owner_client):
    """An older client that sends no id keeps the previous behaviour."""
    bid, lid = await _batch()
    r = await owner_client.post(f"/api/deliveries/{bid}/scan-batch", json={"counts": {"H2B-1": 1}})
    assert r.status_code == 200 and r.json().get("duplicate") is not True
    assert await _scanned(lid) == 1.0
