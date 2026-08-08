# -*- coding: utf-8 -*-
"""P7: documents attach to deliveries many-to-many. The point of the join table is that one invoice
can cover three deliveries and one delivery can be split across two — which a nullable column on the
batch could not express without a migration across live goods-in records."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch
from app.routers import deliveries

SUP = "ТЕСТ-P7"


async def _batch(ref="P7"):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, depot_id=1, reference=ref, doc_date="", source="file",
                          status="draft", created_by="t@t")
        s.add(b)
        await s.commit()
        return b.id


# ── Barsy's own ids ──────────────────────────────────────────────────────────
def test_the_doc_types_are_barsys_ids_verbatim():
    """Read off the live Ново зареждане screen 2026-08-07. If these drift, the panel and Barsy stop
    describing the same document the same way."""
    assert deliveries.DOC_TYPES[1] == "Фактура"
    assert deliveries.DOC_TYPES[11] == "Фактура ЧВ"
    assert deliveries.DOC_TYPES[2] == "Стокова разписка"
    assert deliveries.DOC_TYPES[5] == "Дебитно известие"
    assert deliveries.DOC_TYPES[4] == "Кредитно известие"
    assert deliveries.DOC_TYPES[6] == "Митническа декларация"
    assert deliveries.DOC_TYPES[3] == "Друго"
    assert deliveries.DOC_TYPES[-1] == "Неизвестен"


def test_a_goods_note_is_flagged_as_goods_only():
    """A стокова разписка says the things ARRIVED, not that they were billed."""
    assert 2 in deliveries.GOODS_ONLY_TYPES
    assert 1 not in deliveries.GOODS_ONLY_TYPES


@pytest.mark.asyncio
async def test_doc_types_endpoint_lists_them_in_barsys_order(owner_client):
    r = await owner_client.get("/api/deliveries/doc-types")
    ids = [d["id"] for d in r.json()["doc_types"]]
    assert ids == [1, 11, 2, 5, 4, 6, 3, -1]


# ── attaching ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_attach_and_list(owner_client):
    bid = await _batch()
    r = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                json={"doc_type_id": 2, "invoice_ref": "СР-001",
                                      "doc_date": "2026-08-08", "amount": 120.0})
    assert r.status_code == 201, r.text
    assert r.json()["doc_type"] == "Стокова разписка" and r.json()["goods_only"] is True

    docs = (await owner_client.get(f"/api/deliveries/{bid}/documents")).json()["documents"]
    assert len(docs) == 1 and docs[0]["invoice_ref"] == "СР-001"


@pytest.mark.asyncio
async def test_goods_note_now_invoice_later(owner_client):
    """The reason this table exists: the goods event and the money event are different days."""
    bid = await _batch()
    await owner_client.post(f"/api/deliveries/{bid}/documents",
                            json={"doc_type_id": 2, "invoice_ref": "СР-002", "amount": 120.0})
    later = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                    json={"doc_type_id": 1, "invoice_ref": "Ф-900",
                                          "doc_date": "2026-08-15", "amount": 120.0})
    assert later.status_code == 201
    docs = (await owner_client.get(f"/api/deliveries/{bid}/documents")).json()["documents"]
    assert {d["doc_type"] for d in docs} == {"Стокова разписка", "Фактура"}


@pytest.mark.asyncio
async def test_ONE_invoice_across_SEVERAL_deliveries(owner_client):
    """The N:M case in Kris's own words. A column on the batch could not express this."""
    a, b = await _batch("P7-A"), await _batch("P7-B")
    for bid in (a, b):
        r = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                    json={"doc_type_id": 1, "invoice_ref": "Ф-777", "amount": 50.0})
        assert r.status_code == 201, r.text

    out = (await owner_client.get("/api/deliveries/by-document?invoice_ref=Ф-777")).json()
    assert out["count"] == 2
    assert {d["batch_id"] for d in out["deliveries"]} == {a, b}
    assert out["total_amount"] == 100.0


@pytest.mark.asyncio
async def test_ONE_delivery_split_across_TWO_invoices(owner_client):
    """The other direction of the same relationship."""
    bid = await _batch()
    for ref in ("Ф-801", "Ф-802"):
        r = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                    json={"doc_type_id": 1, "invoice_ref": ref, "amount": 60.0})
        assert r.status_code == 201
    docs = (await owner_client.get(f"/api/deliveries/{bid}/documents")).json()["documents"]
    assert {d["invoice_ref"] for d in docs} == {"Ф-801", "Ф-802"}


@pytest.mark.asyncio
async def test_the_same_document_twice_on_ONE_delivery_is_refused(owner_client):
    bid = await _batch()
    body = {"doc_type_id": 1, "invoice_ref": "Ф-DUP", "amount": 10.0}
    assert (await owner_client.post(f"/api/deliveries/{bid}/documents", json=body)).status_code == 201
    assert (await owner_client.post(f"/api/deliveries/{bid}/documents", json=body)).status_code == 409


@pytest.mark.asyncio
async def test_a_credit_note_is_just_another_document_type(owner_client):
    """Where a resolved рекламация belongs, in Barsy's own vocabulary."""
    bid = await _batch()
    r = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                json={"doc_type_id": 4, "invoice_ref": "КИ-1", "amount": 24.0,
                                      "note": "две счупени чаши"})
    assert r.status_code == 201 and r.json()["doc_type"] == "Кредитно известие"


@pytest.mark.asyncio
async def test_a_document_can_be_attached_after_finalise(owner_client):
    """Deliberate: the фактура routinely arrives days after the goods."""
    bid = await _batch()
    async with async_session_maker() as s:
        (await s.get(DeliveryBatch, bid)).status = "finalised"
        await s.commit()
    r = await owner_client.post(f"/api/deliveries/{bid}/documents",
                                json={"doc_type_id": 1, "invoice_ref": "Ф-LATE", "amount": 99.0})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_validation(owner_client):
    bid = await _batch()
    for body in ({"doc_type_id": 999, "invoice_ref": "X"},      # unknown type
                 {"doc_type_id": 1, "invoice_ref": "   "},      # no reference
                 {"doc_type_id": 1, "invoice_ref": "Y", "amount": -1}):
        r = await owner_client.post(f"/api/deliveries/{bid}/documents", json=body)
        assert r.status_code == 400, body
    assert (await owner_client.get("/api/deliveries/by-document?invoice_ref=")).status_code == 400


@pytest.mark.asyncio
async def test_detaching_is_owner_only_and_works(owner_client):
    bid = await _batch()
    d = (await owner_client.post(f"/api/deliveries/{bid}/documents",
                                 json={"doc_type_id": 1, "invoice_ref": "Ф-DEL"})).json()
    assert (await owner_client.delete(f"/api/deliveries/{bid}/documents/{d['id']}")).status_code == 200
    assert (await owner_client.get(f"/api/deliveries/{bid}/documents")).json()["documents"] == []
