# -*- coding: utf-8 -*-
"""H3: a product WE created must never be invisible to the flow that created it — not at import
(where it would mint a second Barsy article) and not at the scanner (where it was a dead end)."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine, IntakeDraft
from app.routers import deliveries
from app.services import delivery_parse as DP

SUP = "ТЕСТ-H3"
NEW_EAN = "7350000000019"        # deliberately absent from barcodes.jsonl
SUP_REF = "H3-REF-1"


async def _pushed_draft(barcode=NEW_EAN, code=SUP_REF, article_id=4242):
    """A product this panel already created in Barsy — present locally, absent from the snapshot."""
    async with async_session_maker() as s:
        d = IntakeDraft(code=code, barcode=barcode, name="Създаден от нас",
                        status="pushed", approved_by="t@t", barsy_article_id=article_id)
        s.add(d)
        await s.commit()
        return d.id


async def _batch_with_line(**kw):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, supplier_id=None, depot_id=1, reference="H3",
                          doc_date="", source="file", status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ln = DeliveryLine(batch_id=b.id, name="Ред", qty=kw.pop("qty", 2.0), price=1.0,
                          is_new=kw.pop("is_new", False), article_id=kw.pop("article_id", 4242),
                          status="pending", **kw)
        s.add(ln)
        await s.commit()
        return b.id, ln.id


# ── A6.1 the caches must notice a changed snapshot ───────────────────────────
def test_caches_are_keyed_on_mtime_not_pinned_forever():
    a = DP.barcode_index()
    assert DP.barcode_index() is a, "same mtime should still hit the cache"
    DP.refresh_catalog_caches()
    assert DP.barcode_index() is not a, "after a refresh it must re-read the file"
    assert DP.barcode_index()["ean_to_code"], "and still return a populated index"


# ── A6.2 our own pushed products resolve at import ───────────────────────────
@pytest.mark.asyncio
async def test_import_adopts_a_product_we_created_instead_of_minting_a_second(owner_client):
    """The worst outcome in the flow: the same physical product getting a SECOND Barsy article."""
    await _pushed_draft()
    parsed = [{"supplier_code": SUP_REF, "name": "Създаден от нас", "qty": 3.0, "price": 1.0,
               "barcode": NEW_EAN, "is_new": True, "match_code": "",
               "match_method": "", "match_confidence": ""}]
    async with async_session_maker() as s:
        rescued = await deliveries._adopt_known_products(s, parsed)
    assert rescued == 1
    assert parsed[0]["is_new"] is False
    assert parsed[0]["article_id"] == 4242
    assert parsed[0]["match_method"] == "intake_draft"


@pytest.mark.asyncio
async def test_adoption_leaves_a_genuinely_new_product_alone(owner_client):
    parsed = [{"supplier_code": "NEVER-SEEN-H3", "name": "Наистина нов", "qty": 1.0, "price": 1.0,
               "barcode": "7350000000992", "is_new": True, "match_code": "",
               "match_method": "", "match_confidence": ""}]
    async with async_session_maker() as s:
        assert await deliveries._adopt_known_products(s, parsed) == 0
    assert parsed[0]["is_new"] is True


@pytest.mark.asyncio
async def test_a_claimed_but_uncreated_draft_is_not_adopted(owner_client):
    """barsy_article_id = -1 is the in-flight claim sentinel, not a real article."""
    await _pushed_draft(barcode="7350000000026", code="H3-CLAIMED", article_id=-1)
    parsed = [{"supplier_code": "H3-CLAIMED", "name": "В процес", "qty": 1.0, "price": 1.0,
               "barcode": "7350000000026", "is_new": True, "match_code": "",
               "match_method": "", "match_confidence": ""}]
    async with async_session_maker() as s:
        assert await deliveries._adopt_known_products(s, parsed) == 0


# ── A6.3 scanning a product we created ───────────────────────────────────────
@pytest.mark.asyncio
async def test_scan_resolves_a_barcode_only_our_drafts_know(owner_client):
    await _pushed_draft(barcode="7350000000033", code="H3-SCAN")
    bid, lid = await _batch_with_line(supplier_code="H3-SCAN", match_code="", barcode="")
    r = await owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": "7350000000033"})
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is True
    async with async_session_maker() as s:
        assert (await s.get(DeliveryLine, lid)).scanned_qty == 1.0


# ── A7 the barcode dead end ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_attach_works_on_a_created_product_no_longer_is_new(owner_client):
    """intake flips is_new off the moment the article is created; the worker is still holding it."""
    bid, lid = await _batch_with_line(supplier_code="H3-ATT", match_code="", barcode="", is_new=False)
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/attach-barcode",
                                json={"barcode": "7350000000040"})
    assert r.status_code == 200, r.text
    assert r.json()["line"]["barcode"] == "7350000000040"
    assert r.json()["line"]["scanned_qty"] == 1.0


@pytest.mark.asyncio
async def test_attach_refuses_a_line_that_already_has_a_barcode(owner_client):
    bid, lid = await _batch_with_line(supplier_code="H3-HAS", match_code="", barcode="7350000000057")
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/attach-barcode",
                                json={"barcode": "7350000000064"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unknown_dialog_offers_barcodeless_lines_not_just_new_ones(owner_client):
    """The dead end read 'няма нови продукти' while the product sat in the worker's hand."""
    bid, lid = await _batch_with_line(supplier_code="H3-DLG", match_code="", barcode="", is_new=False)
    r = await owner_client.post(f"/api/deliveries/{bid}/scan", json={"barcode": "7350000000071"})
    body = r.json()
    assert body["unknown"] is True
    assert [x["id"] for x in body["new_lines"]] == [lid]
