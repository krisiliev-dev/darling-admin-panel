# -*- coding: utf-8 -*-
"""Delivery-based intake: Excel parse (new-vs-existing split) + batch commit to Barsy as ONE
storeload. The gate (DELIVERY_BARSY_LIVE) defaults OFF, so commit dry-runs unless flipped."""
import io
from types import SimpleNamespace

import openpyxl
import pytest

from app.routers import intake_delivery


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# a real catalogue code from the seed (see intake_delivery._catalog_index)
EXISTING_CODE = "0191500"
EXISTING_NAME = "AB-BAIN MARIE -Касерола с двойни стени (46116)"


def _upload(content: bytes, filename="dostavka.xlsx"):
    return {"file": (filename, content,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


# ------------------------------------------------------------------ parse ---
@pytest.mark.asyncio
async def test_parse_splits_new_vs_existing(owner_client):
    xlsx = _xlsx([
        ["Код", "Наименование", "Количество", "Доставна цена"],
        [EXISTING_CODE, EXISTING_NAME, 5, "14,48"],          # existing (code hit)
        ["NEW-777", "Съвсем нов артикул за тест", 12, 3.20],   # brand new
    ])
    r = await owner_client.post("/api/intake/delivery/parse", files=_upload(xlsx))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["positional"] is False
    assert body["columns"]["name"] == "Наименование"        # header label surfaced
    assert body["summary"] == {"total": 2, "new": 1, "existing": 1, "total_qty": 17.0}
    by_code = {row["supplier_code"]: row for row in body["rows"]}
    assert by_code[EXISTING_CODE]["is_new"] is False
    assert by_code[EXISTING_CODE]["match_method"] == "code"
    assert by_code[EXISTING_CODE]["match_confidence"] == "high"
    new = by_code["NEW-777"]
    assert new["is_new"] is True
    assert new["qty"] == 12 and new["delivery_price"] == 3.20


@pytest.mark.asyncio
async def test_parse_matches_by_supplier_code_mapping(owner_client):
    """The strongest key: supplier + their own article code → our code (from supplier_codes.jsonl)."""
    import json
    from app.routers.intake_delivery import _SUPPLIER_CODES_PATH
    with open(_SUPPLIER_CODES_PATH, encoding="utf-8") as f:
        row = json.loads(f.readline())            # a real (supplier, ref, code) triple
    xlsx = _xlsx([
        ["Код", "Наименование", "Количество", "Цена"],
        [row["ref"], row["rname"], 3, ""],        # sheet omits price → mapping fills it
    ])
    r = await owner_client.post("/api/intake/delivery/parse",
                                data={"supplier": row["supplier"]}, files=_upload(xlsx))
    assert r.status_code == 200, r.text
    got = r.json()["rows"][0]
    assert got["is_new"] is False
    assert got["match_code"] == row["code"]
    assert got["match_confidence"] == "high"      # supplier + code = high
    assert got["delivery_price"] > 0              # back-filled from the mapping's default price


@pytest.mark.asyncio
async def test_parse_positional_when_no_header(owner_client):
    # no recognisable header row → columns assumed [code, name, qty, price]; every row is data
    xlsx = _xlsx([
        ["X-1", "Ксзщ несъществуващ артикул алфа", 2, 1.5],
        ["X-2", "Ждрп несъществуващ артикул бета", 4, 2.0],
    ])
    r = await owner_client.post("/api/intake/delivery/parse", files=_upload(xlsx))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["positional"] is True
    assert body["summary"]["total"] == 2
    assert all(row["is_new"] for row in body["rows"])        # unknown codes/names → new


@pytest.mark.asyncio
async def test_parse_flags_missing_quantity(owner_client):
    xlsx = _xlsx([
        ["Код", "Наименование", "Количество", "Цена"],
        ["Z-9", "Нещо без количество", 0, 5],
    ])
    r = await owner_client.post("/api/intake/delivery/parse", files=_upload(xlsx))
    assert r.status_code == 200, r.text
    assert "липсва количество" in r.json()["rows"][0]["warnings"]


@pytest.mark.asyncio
async def test_parse_rejects_non_xlsx(owner_client):
    r = await owner_client.post("/api/intake/delivery/parse",
                                files=_upload(b"not a spreadsheet", filename="notes.txt"))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_parse_requires_auth(client):
    r = await client.post("/api/intake/delivery/parse", files=_upload(_xlsx([["a", "b", 1, 1]])))
    assert r.status_code == 401


# ------------------------------------------------------------------ commit (dry-run) ---
def _commit_body():
    return {
        "supplier": "Тестов доставчик", "reference": "INV-42", "date": "2026-07-25",
        "confirm": True,
        "lines": [
            {"is_new": False, "article_id": 555, "supplier_code": EXISTING_CODE,
             "name": EXISTING_NAME, "qty": 5, "delivery_price": 14.48},
            {"is_new": True, "code": "NEW-777", "name": "Нов артикул", "qty": 12,
             "delivery_price": 3.20, "sale_price": "6.99", "unit": "бр"},
        ],
    }


@pytest.mark.asyncio
async def test_commit_dry_run_writes_nothing(owner_client, barsy_recorder):
    # gate defaults OFF → dry-run regardless of confirm
    r = await owner_client.post("/api/intake/delivery/commit", json=_commit_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert barsy_recorder.calls == []                         # no ERP write at all
    # verified Storeloads_create shape: one store_load header + all rows in one articles[] array
    payload = body["storeload_payload"]
    assert payload["action_type"] == "save_and_close"
    assert payload["store_load"]["depot_id"] == 1
    assert payload["store_load"]["operation_type"] == 1   # receipt (part of minimal validated shape)
    assert payload["store_load"]["doc_type_id"] == 1      # Фактура
    arts = payload["articles"]
    assert len(arts) == 2
    assert arts[0]["article_id"] == 555 and arts[0]["amount"] == "5.0"
    assert arts[0]["current_price"] == "14.48"                # delivery/cost price
    assert any("DELIVERY_BARSY_LIVE" in w for w in body["warnings"])


@pytest.mark.asyncio
async def test_commit_rejects_new_line_without_code(owner_client):
    body = _commit_body()
    body["lines"][1]["code"] = ""
    body["lines"][1]["supplier_code"] = ""
    r = await owner_client.post("/api/intake/delivery/commit", json=body)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_commit_rejects_zero_quantity(owner_client):
    body = _commit_body()
    body["lines"][0]["qty"] = 0
    r = await owner_client.post("/api/intake/delivery/commit", json=body)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_commit_requires_owner(staff_client):
    r = await staff_client.post("/api/intake/delivery/commit", json=_commit_body())
    assert r.status_code == 403


# ------------------------------------------------------------------ commit (live) ---
@pytest.mark.asyncio
async def test_commit_live_creates_articles_then_one_storeload(owner_client, barsy_recorder, monkeypatch):
    # flip the gate ON for this test only
    monkeypatch.setattr(intake_delivery, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1))
    barsy_recorder.set_return("Articles_create", {"article_id": 9001})
    barsy_recorder.set_return("Storeloads_create", {"storeload_id": 700})

    r = await owner_client.post("/api/intake/delivery/commit", json=_commit_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["storeload_id"] == 700
    # exactly one article created (the single new line) + exactly one storeload
    assert barsy_recorder.operations.count("Articles_create") == 1
    assert barsy_recorder.operations.count("Storeloads_create") == 1
    # the one storeload rows both existing (555) and the freshly-created new article (9001)
    arts = barsy_recorder.body_for("Storeloads_create")["articles"]
    ids = {a["article_id"] for a in arts}
    assert ids == {555, 9001}


@pytest.mark.asyncio
async def test_commit_live_resolves_supplier_id_into_header(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(intake_delivery, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1))
    barsy_recorder.set_return("Suppliers_getlist",
                              [{"supplier_id": 39, "supplier_name": "Тестов доставчик"}])
    barsy_recorder.set_return("Articles_create", {"article_id": 9001})
    barsy_recorder.set_return("Storeloads_create", 700)          # bare-int return also parses
    r = await owner_client.post("/api/intake/delivery/commit", json=_commit_body())
    assert r.status_code == 200, r.text
    assert r.json()["storeload_id"] == 700
    # the supplier name resolved to id 39 and rode into the storeload header
    assert barsy_recorder.body_for("Storeloads_create")["store_load"]["supplier_id"] == 39


@pytest.mark.asyncio
async def test_commit_live_needs_confirm(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(intake_delivery, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_depot_id=1))
    body = _commit_body()
    body["confirm"] = False
    r = await owner_client.post("/api/intake/delivery/commit", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True                        # gate on but no confirm → still dry-run
    assert barsy_recorder.calls == []
