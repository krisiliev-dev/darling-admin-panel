# -*- coding: utf-8 -*-
"""Доставки (receiving): upload → new-vs-existing split → barcode scan-confirm → finalise gate →
one Barsy storeload. Uses the bundled supplier_codes.jsonl + barcodes.jsonl for matching/scanning."""
import io
from types import SimpleNamespace

import openpyxl
import pytest

from app.db import async_session_maker
from app.models import DeliveryLine
from app.routers import deliveries

# real anchors from the bundled data
SUP = "АНИС"
EXIST_REF = "05203"       # supplier code → our code 14153
EXIST_CODE = "14153"
EXIST_EAN = "8696287052034"


def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _upload(content, filename="dostavka.xlsx",
            ct="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
    return {"file": (filename, content, ct)}


def _sheet():
    return _xlsx([
        ["Код", "Наименование", "Количество", "Цена"],
        [EXIST_REF, "Закачалка", 3, "2,50"],                    # existing (АНИС 05203 → 14153)
        ["NOVO-1", "Нов непознат артикул хикс", 5, 4.20],        # new
    ])


async def _create(client, data=None):
    d = {"supplier": SUP, "depot_id": 1, "reference": "INV-1", "doc_date": "2026-07-25"}
    if data:
        d.update(data)
    r = await client.post("/api/deliveries", data=d, files=_upload(_sheet()))
    assert r.status_code == 201, r.text
    return r.json()


async def _set_article_id(line_id, aid):
    async with async_session_maker() as s:
        ln = await s.get(DeliveryLine, line_id)
        ln.article_id = aid
        ln.is_new = False
        await s.commit()


# ------------------------------------------------------------------ create + list + detail ---
@pytest.mark.asyncio
async def test_create_splits_and_stages_new(owner_client):
    b = await _create(owner_client)
    assert b["total"] == 2 and b["new"] == 1 and b["existing"] == 1
    assert b["status"] == "awaiting_products"                    # a new product is pending
    lines = b["lines"]
    assert lines[0]["is_new"] is False                          # existing sorted first
    assert lines[0]["match_code"] == EXIST_CODE
    assert lines[0]["barcode"] == EXIST_EAN                     # known EAN attached for scanning
    new = lines[1]
    assert new["is_new"] is True and new["intake_draft_id"] is not None  # staged for Приемане


@pytest.mark.asyncio
async def test_create_from_csv(owner_client):
    csv_bytes = ("Код;Наименование;Количество;Цена\n"
                 f"{EXIST_REF};Закачалка;3;2,50\n").encode("utf-8")
    r = await owner_client.post("/api/deliveries", data={"supplier": SUP},
                                files=_upload(csv_bytes, "d.csv", "text/csv"))
    assert r.status_code == 201, r.text
    assert r.json()["existing"] == 1


@pytest.mark.asyncio
async def test_list_and_get(owner_client):
    b = await _create(owner_client)
    lst = (await owner_client.get("/api/deliveries")).json()["deliveries"]
    assert any(d["id"] == b["id"] and d["total"] == 2 for d in lst)
    got = (await owner_client.get(f"/api/deliveries/{b['id']}")).json()
    assert got["id"] == b["id"] and len(got["lines"]) == 2


# ------------------------------------------------------------------ scan-confirm ---
@pytest.mark.asyncio
async def test_scan_confirms_and_flags_mismatch(owner_client):
    b = await _create(owner_client)
    line = next(l for l in b["lines"] if not l["is_new"])       # qty 3, barcode = EXIST_EAN
    # scan the EAN twice → still short of 3 → mismatch
    for _ in range(2):
        r = await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})
        assert r.json()["matched"] is True
    st = (await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})).json()
    assert st["line"]["scanned_qty"] == 3 and st["line"]["status"] == "confirmed"
    # one more → over-count → mismatch (red)
    over = (await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})).json()
    assert over["line"]["status"] == "mismatch"


@pytest.mark.asyncio
async def test_scan_resolves_ean_to_matched_code(owner_client):
    # even if the line carried no barcode, a scanned EAN resolves via the catalogue → its code's line
    b = await _create(owner_client)
    line = next(l for l in b["lines"] if not l["is_new"])
    await _set_barcode_blank(line["id"])
    r = (await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})).json()
    assert r["matched"] is True and r["line"]["id"] == line["id"]


async def _set_barcode_blank(line_id):
    async with async_session_maker() as s:
        ln = await s.get(DeliveryLine, line_id); ln.barcode = ""; await s.commit()


@pytest.mark.asyncio
async def test_scan_unknown_offers_new_lines(owner_client):
    b = await _create(owner_client)
    r = (await owner_client.post(f"/api/deliveries/{b['id']}/scan",
                                 json={"barcode": "0000000000000"})).json()
    assert r["unknown"] is True
    assert any(l["is_new"] for l in r["new_lines"])


@pytest.mark.asyncio
async def test_attach_barcode_to_new_line(owner_client):
    b = await _create(owner_client)
    new = next(l for l in b["lines"] if l["is_new"])
    r = await owner_client.post(f"/api/deliveries/{b['id']}/lines/{new['id']}/attach-barcode",
                                json={"barcode": "1112223334445"})
    assert r.status_code == 200, r.text
    body = r.json()["line"]
    assert body["barcode"] == "1112223334445" and body["scanned_qty"] == 1


# ------------------------------------------------------------------ edit / delete ---
@pytest.mark.asyncio
async def test_edit_line_recolours(owner_client):
    b = await _create(owner_client)
    line = next(l for l in b["lines"] if not l["is_new"])
    # scan to 3 (confirmed), then edit claimed qty to 5 → now a mismatch
    for _ in range(3):
        await owner_client.post(f"/api/deliveries/{b['id']}/scan", json={"barcode": EXIST_EAN})
    r = await owner_client.patch(f"/api/deliveries/{b['id']}/lines/{line['id']}", json={"qty": 5})
    assert r.json()["line"]["status"] == "mismatch"


@pytest.mark.asyncio
async def test_delete_draft_removes_lines_and_drafts(owner_client):
    from app.models import IntakeDraft
    b = await _create(owner_client)
    draft_id = next(l["intake_draft_id"] for l in b["lines"] if l["is_new"])
    r = await owner_client.delete(f"/api/deliveries/{b['id']}")
    assert r.status_code == 200
    assert (await owner_client.get(f"/api/deliveries/{b['id']}")).status_code == 404
    async with async_session_maker() as s:
        assert await s.get(IntakeDraft, draft_id) is None       # staged draft cleaned up


@pytest.mark.asyncio
async def test_delete_requires_owner(staff_client, owner_client):
    b = await _create(owner_client)
    assert (await staff_client.delete(f"/api/deliveries/{b['id']}")).status_code == 403


# ------------------------------------------------------------------ finalise gate + storeload ---
@pytest.mark.asyncio
async def test_finalise_blocked_while_new_products_pending(owner_client):
    b = await _create(owner_client)
    r = await owner_client.post(f"/api/deliveries/{b['id']}/finalise", json={"confirm": True})
    assert r.status_code == 409
    assert "нови продукта" in r.json()["detail"]


@pytest.mark.asyncio
async def test_finalise_dry_run_after_products_added(owner_client, barsy_recorder):
    b = await _create(owner_client)
    # simulate: new product enriched+created in Barsy (article_id written back); existing resolves via catalog
    for l in b["lines"]:
        if l["is_new"]:
            await _set_article_id(l["id"], 9001)
    barsy_recorder.set_return("Articles_getlist", [{"code": EXIST_CODE, "article_id": 555}])
    r = await owner_client.post(f"/api/deliveries/{b['id']}/finalise", json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    ids = {a["article_id"] for a in body["storeload_payload"]["articles"]}
    assert ids == {9001, 555}                                   # new + resolved-existing
    assert body["storeload_payload"]["action_type"] == "save_and_close"


@pytest.mark.asyncio
async def test_finalise_live_sends_one_storeload(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(deliveries, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1))
    b = await _create(owner_client)
    for l in b["lines"]:
        if l["is_new"]:
            await _set_article_id(l["id"], 9001)
    barsy_recorder.set_return("Articles_getlist", [{"code": EXIST_CODE, "article_id": 555}])
    barsy_recorder.set_return("Suppliers_getlist", [{"supplier_id": 39, "supplier_name": SUP}])
    barsy_recorder.set_return("Storeloads_create", {"storeload_id": 700})
    r = await owner_client.post(f"/api/deliveries/{b['id']}/finalise", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["storeload_id"] == 700
    assert barsy_recorder.operations.count("Storeloads_create") == 1
    hdr = barsy_recorder.body_for("Storeloads_create")["store_load"]
    assert hdr["supplier_id"] == 39 and hdr["operation_type"] == 1
    # batch now finalised
    assert (await owner_client.get(f"/api/deliveries/{b['id']}")).json()["status"] == "finalised"


@pytest.mark.asyncio
async def test_finalise_requires_owner(staff_client, owner_client):
    b = await _create(owner_client)
    assert (await staff_client.post(f"/api/deliveries/{b['id']}/finalise",
                                    json={"confirm": True})).status_code == 403


# ------------------------------------------------------------------ from-rows (edited/OCR) ---
@pytest.mark.asyncio
async def test_from_rows_creates_and_matches(owner_client):
    r = await owner_client.post("/api/deliveries/from-rows", json={
        "supplier": SUP, "reference": "OCR-1", "source": "photo",
        "rows": [
            {"supplier_code": EXIST_REF, "name": "Закачалка", "qty": 3, "price": 2.5},
            {"supplier_code": "NOVO-2", "name": "Нов артикул от снимка", "qty": 7, "price": 1.1},
        ]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "photo" and body["new"] == 1 and body["existing"] == 1


@pytest.mark.asyncio
async def test_full_new_product_loop_clears_finalise_gate(owner_client, barsy_recorder):
    """delivery → new product staged as draft → enrich+approve (PUT) → push to Barsy writes the
    article_id back onto the line → finalise gate clears."""
    b = await _create(owner_client)
    new_line = next(l for l in b["lines"] if l["is_new"])
    draft_id = new_line["intake_draft_id"]

    # the new product surfaces in the Приемане "from delivery" queue
    fd = (await owner_client.get("/api/intake/from-delivery")).json()["drafts"]
    assert any(d["id"] == draft_id for d in fd)

    # enrich + approve the EXISTING draft in place (keeps its delivery_line_id)
    r = await owner_client.put(f"/api/intake/drafts/{draft_id}", json={
        "code": "OUR-NEW-1", "name": "Готов нов продукт", "price": "9.99", "unit": "бр",
        "category": "Дом", "description": "…", "attributes": [], "barcode": "999"})
    assert r.status_code == 200, r.text

    # owner pushes it to Barsy → Articles_create → article_id written back to the delivery line
    barsy_recorder.set_return("Articles_create", {"article_id": 8080})
    p = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert p.status_code == 200, p.text

    async with async_session_maker() as s:
        ln = await s.get(DeliveryLine, new_line["id"])
        assert ln.article_id == 8080 and ln.is_new is False

    # now the delivery gate clears (existing line resolves via catalog)
    barsy_recorder.set_return("Articles_getlist", [{"code": EXIST_CODE, "article_id": 555}])
    fin = await owner_client.post(f"/api/deliveries/{b['id']}/finalise", json={"confirm": True})
    assert fin.status_code == 200, fin.text
    ids = {a["article_id"] for a in fin.json()["storeload_payload"]["articles"]}
    assert ids == {8080, 555}


@pytest.mark.asyncio
async def test_ocr_endpoint_degrades_gracefully_when_deps_absent(owner_client):
    # img2table/rapidocr aren't installed in CI/local → the endpoint 503s with a clear message
    # (on the deployed image the OCR layer is present and it returns extracted rows instead).
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = await owner_client.post("/api/deliveries/ocr",
                                files={"file": ("note.png", png, "image/png")})
    assert r.status_code in (503, 422)      # 503 = deps absent (expected here); 422 if present but unreadable
    if r.status_code == 503:
        assert "OCR" in r.json()["detail"]
