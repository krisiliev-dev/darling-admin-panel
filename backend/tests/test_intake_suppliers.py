# -*- coding: utf-8 -*-
"""Draft supplier rows (Нов продукт editor's Доставчици table). Properties under test:
CRUD round-trips; adding the SAME supplier twice edits in place instead of duplicating;
suggestions come from the supplier catalogue by our code and disappear once added; and the
Barsy create body carries ref_num + supplier-name aliases from the table (delivery line, when
present, still outranks the table — that contract is asserted here too)."""
import json
from types import SimpleNamespace

import pytest

import app.routers.intake as intake_mod
from app.db import async_session_maker
from app.models import IntakeDraft


@pytest.fixture(autouse=True)
def _known_suppliers(monkeypatch):
    """The supplier field restricts to actual suppliers — give the suite a fixed known set so it
    doesn't depend on the bundled supplier_codes.jsonl. (Tests needing other data patch on top.)"""
    from app.services import supplier_catalog as sc
    monkeypatch.setattr(sc, "supplier_catalog",
                        lambda: {"by_supplier": {"Мира-Н": [], "Анис": [], "Ин Хаус": []},
                                 "by_code": {}})


async def _set_status(draft_id: int, status: str) -> None:
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, draft_id)
        d.status = status
        await s.commit()


@pytest.mark.asyncio
async def test_supplier_rows_crud(staff_client, make_draft):
    did = await make_draft(name="Купа керамична", status="editing")
    r = await staff_client.get(f"/api/intake/drafts/{did}/suppliers")
    assert r.status_code == 200 and r.json() == {"suppliers": [], "suggestions": []}

    r = await staff_client.post(f"/api/intake/drafts/{did}/suppliers", json={
        "supplier": "Мира-Н", "supplier_code": "HC-16241", "supplier_name": "Metal caddy"})
    assert r.status_code == 201 and r.json()["updated"] is False
    r2 = await staff_client.post(f"/api/intake/drafts/{did}/suppliers", json={
        "supplier": "Анис", "supplier_code": "A-77", "supplier_name": ""})
    assert r2.status_code == 201

    got = (await staff_client.get(f"/api/intake/drafts/{did}/suppliers")).json()["suppliers"]
    assert [g["supplier"] for g in got] == ["Мира-Н", "Анис"]

    # same supplier again (case-insensitive) = EDIT, not a duplicate row
    r3 = await staff_client.post(f"/api/intake/drafts/{did}/suppliers", json={
        "supplier": "мира-н", "supplier_code": "HC-9999", "supplier_name": "Caddy v2"})
    assert r3.status_code == 201 and r3.json()["updated"] is True
    got = (await staff_client.get(f"/api/intake/drafts/{did}/suppliers")).json()["suppliers"]
    assert len(got) == 2 and got[0]["supplier_code"] == "HC-9999"

    # delete removes the row; deleting a row of ANOTHER draft is refused
    rid = got[1]["id"]
    assert (await staff_client.delete(f"/api/intake/drafts/{did}/suppliers/{rid}")).status_code == 200
    other = await make_draft(name="Друг", status="editing")
    assert (await staff_client.delete(
        f"/api/intake/drafts/{other}/suppliers/{got[0]['id']}")).status_code == 404
    got = (await staff_client.get(f"/api/intake/drafts/{did}/suppliers")).json()["suppliers"]
    assert [g["supplier"] for g in got] == ["Мира-Н"]


@pytest.mark.asyncio
async def test_supplier_input_guards(staff_client, make_draft):
    did = await make_draft(name="X", status="editing")
    r = await staff_client.post(f"/api/intake/drafts/{did}/suppliers",
                                json={"supplier": "   "})
    assert r.status_code == 400
    assert (await staff_client.get("/api/intake/drafts/424242/suppliers")).status_code == 404


@pytest.mark.asyncio
async def test_supplier_must_be_a_known_supplier(staff_client, make_draft):
    """Free-typed / unknown suppliers are refused — only actual suppliers we have."""
    did = await make_draft(name="Y", status="editing")
    r = await staff_client.post(f"/api/intake/drafts/{did}/suppliers",
                                json={"supplier": "Няма такъв доставчик ООД"})
    assert r.status_code == 400
    ok = await staff_client.post(f"/api/intake/drafts/{did}/suppliers",
                                 json={"supplier": "Анис"})
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_known_suppliers_endpoint(staff_client):
    r = await staff_client.get("/api/intake/suppliers/known")
    assert r.status_code == 200
    names = r.json()["suppliers"]
    assert "Анис" in names and "Мира-Н" in names


@pytest.mark.asyncio
async def test_catalogue_suppliers_are_auto_added_once(staff_client, make_draft, monkeypatch):
    """A product with a known code gets its catalogue suppliers AS ROWS on first load — no
    suggestion buttons (owner 2026-08-03). A deliberate removal is respected: rows exist, so
    the next load does NOT re-add."""
    from app.services import supplier_catalog as sc
    monkeypatch.setattr(sc, "supplier_catalog", lambda: {"by_supplier": {}, "by_code": {
        "019060": {"Мира-Н": {"code": "019060", "ref": "HC-1", "name": "Bowl", "dprice": 1.0},
                   "Ин Хаус": {"code": "019060", "ref": "IH-2", "name": "Купа", "dprice": 2.0}}}})

    did = await make_draft(name="Купа", code="019060", status="editing")
    out = (await staff_client.get(f"/api/intake/drafts/{did}/suppliers")).json()
    assert out["suggestions"] == []
    assert {s["supplier"] for s in out["suppliers"]} == {"Мира-Н", "Ин Хаус"}
    assert next(s for s in out["suppliers"] if s["supplier"] == "Мира-Н")["supplier_code"] == "HC-1"

    rid = next(s for s in out["suppliers"] if s["supplier"] == "Ин Хаус")["id"]
    assert (await staff_client.delete(f"/api/intake/drafts/{did}/suppliers/{rid}")).status_code == 200
    again = (await staff_client.get(f"/api/intake/drafts/{did}/suppliers")).json()["suppliers"]
    assert [s["supplier"] for s in again] == ["Мира-Н"]   # removal respected, not re-added


@pytest.mark.asyncio
async def test_get_single_draft(staff_client, make_draft):
    did = await make_draft(name="Чаша", status="enriched", price="3.20")
    r = await staff_client.get(f"/api/intake/drafts/{did}")
    assert r.status_code == 200 and r.json()["name"] == "Чаша" and r.json()["status"] == "enriched"
    assert (await staff_client.get("/api/intake/drafts/909090")).status_code == 404


@pytest.mark.asyncio
async def test_stash_saves_fields_without_approving(staff_client, make_draft):
    """Stash = save current work, change NOTHING else: status stays, no Barsy write, no image
    requirement (it exists so leaving the editor mid-work loses nothing)."""
    did = await make_draft(name="Старо име", status="enriched")
    body = {"code": "", "barcode": "", "price": "9.99", "unit": "бр", "name": "Ново име",
            "category": "", "shopify_category_id": "", "brand": "Bursev", "series": "",
            "description": "тест", "attributes": [], "meta_title": "", "meta_description": ""}
    r = await staff_client.patch(f"/api/intake/drafts/{did}/stash", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Ново име" and out["price"] == "9.99" and out["status"] == "enriched"
    assert out["completed"] is False

    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        d.status = "pushed"
        await s.commit()
    assert (await staff_client.patch(f"/api/intake/drafts/{did}/stash", json=body)).status_code == 409


_LIVE = SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0,
                        barsy_depot_id=1, barsy_currency_id=1, barsy_price_mode=1)


@pytest.mark.asyncio
async def test_push_body_carries_supplier_table(owner_client, make_draft, mock_barsy, monkeypatch):
    """The Articles_create envelope gets ref_num from the FIRST supplier row with a code and the
    differing supplier product-names as aliases."""
    monkeypatch.setattr(intake_mod, "get_settings", lambda: _LIVE)
    did = await make_draft(
        name="Купа керамична 24см", status="approved", price="12.50",
        attributes_json=json.dumps([], ensure_ascii=False))
    for row in ({"supplier": "Мира-Н", "supplier_code": "", "supplier_name": "Купа керамична 24см"},
                {"supplier": "Анис", "supplier_code": "AN-55", "supplier_name": "Bowl ceramic"}):
        assert (await owner_client.post(
            f"/api/intake/drafts/{did}/suppliers", json=row)).status_code == 201

    r = await owner_client.post(f"/api/intake/drafts/{did}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    op, body = mock_barsy[-1]
    assert op == "Articles_create"
    art = body["article"]
    assert art["ref_num"] == "AN-55"                       # first row WITH a code
    assert art["article_aliases"] == ["Bowl ceramic"]      # own-name row is not an alias


@pytest.mark.asyncio
async def test_enriched_draft_lists_and_is_not_completed(staff_client, make_draft):
    """A pipeline-filled draft (status='enriched') must APPEAR in /drafts as awaiting approval —
    not completed, not hidden like 'editing' drafts."""
    did = await make_draft(name="Чаша стъклена 250мл", status="enriched",
                           description="Описание от пайплайна.", price="3.20")
    hidden = await make_draft(name="празна", status="editing")
    rows = (await staff_client.get("/api/intake/drafts")).json()["drafts"]
    ids = {d["id"]: d for d in rows}
    assert did in ids and hidden not in ids
    assert ids[did]["completed"] is False and ids[did]["status"] == "enriched"
