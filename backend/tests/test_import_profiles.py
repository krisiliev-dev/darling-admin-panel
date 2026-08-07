# -*- coding: utf-8 -*-
"""Per-supplier import profiles: preview, save/reuse a mapping, the leading-blank-row fix, and
the three sheet shapes the owner named (…/Цена, …/Цена/Баркод, …/Баркод-no-price). Anchors from
the bundled data: АНИС 05203 → our 14153, EAN 8696287052034."""
import io

import openpyxl
import pytest

from app.db import async_session_maker
from app.models import SupplierImportProfile

SUP = "АНИС"
EXIST_REF = "05203"
EXIST_CODE = "14153"
EXIST_EAN = "8696287052034"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _up(content, name="d.xlsx", ct=CT):
    return {"file": (name, content, ct)}


# ------------------------------------------------------------------ preview ---
@pytest.mark.asyncio
async def test_preview_returns_grid_and_detection(owner_client):
    sheet = _xlsx([["Код", "Наименование", "Количество", "Цена"],
                   [EXIST_REF, "Закачалка", 3, "2,50"]])
    r = await owner_client.post("/api/deliveries/preview", data={"supplier": SUP}, files=_up(sheet))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["columns"][:4] == ["A", "B", "C", "D"]
    assert b["rows"][0][0] == "Код"
    assert b["suggested"]["code"] == 0 and b["suggested"]["name"] == 1
    assert b["suggested"]["data_row"] == 2          # header row 1 → data starts row 2
    assert b["profile"] is None


@pytest.mark.asyncio
async def test_preview_suggests_start_row_past_a_blank_first_row(owner_client):
    # blank row 1, header row 2 → the fix: data starts row 3, header not read as a product
    sheet = _xlsx([[None, None, None, None],
                   ["Код", "Наименование", "Количество", "Цена"],
                   [EXIST_REF, "Закачалка", 3, "2,50"]])
    b = (await owner_client.post("/api/deliveries/preview", data={"supplier": SUP}, files=_up(sheet))).json()
    assert b["suggested"]["data_row"] == 3


# ------------------------------------------------------------------ the blank-row bug, end-to-end ---
@pytest.mark.asyncio
async def test_blank_first_row_no_longer_becomes_a_product(owner_client):
    # NO mapping supplied → auto-detect path. Previously the blank row 1 forced positional mode
    # and the header row 2 became a bogus product. strip_leading_blank fixes it.
    sheet = _xlsx([[None, None, None, None],
                   ["Код", "Наименование", "Количество", "Цена"],
                   [EXIST_REF, "Закачалка", 3, "2,50"]])
    r = await owner_client.post("/api/deliveries", data={"supplier": SUP}, files=_up(sheet))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["total"] == 1                          # just the one real product, not header+product
    assert b["lines"][0]["match_code"] == EXIST_CODE


# ------------------------------------------------------------------ explicit mapping + save/reuse ---
@pytest.mark.asyncio
async def test_explicit_mapping_with_barcode_and_no_header(owner_client):
    # shape: Код · Наименование · Количество · Баркод (no price, no header row)
    sheet = _xlsx([[EXIST_REF, "Закачалка", 3, EXIST_EAN]])
    r = await owner_client.post("/api/deliveries", files=_up(sheet), data={
        "supplier": SUP, "col_code": 0, "col_name": 1, "col_qty": 2,
        "col_price": -1, "col_barcode": 3, "data_row": 1, "save_profile": "true"})
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    assert line["match_code"] == EXIST_CODE
    assert line["barcode"] == EXIST_EAN             # barcode column read into the line
    # the profile was saved
    async with async_session_maker() as sdb:
        from sqlalchemy import select
        p = (await sdb.execute(select(SupplierImportProfile))).scalars().first()
        assert p is not None and p.col_barcode == 3 and p.col_price == -1


@pytest.mark.asyncio
async def test_saved_profile_is_reused_without_a_mapping(owner_client):
    # save a profile via an explicit mapping…
    sheet1 = _xlsx([[EXIST_REF, "Закачалка", 3, EXIST_EAN]])
    await owner_client.post("/api/deliveries", files=_up(sheet1), data={
        "supplier": SUP, "col_code": 0, "col_name": 1, "col_qty": 2,
        "col_price": -1, "col_barcode": 3, "data_row": 1, "save_profile": "true"})
    # …then upload the SAME shape with NO mapping — the saved profile must drive the parse
    sheet2 = _xlsx([[EXIST_REF, "Закачалка", 5, EXIST_EAN]])
    r = await owner_client.post("/api/deliveries", data={"supplier": SUP}, files=_up(sheet2))
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    assert line["qty"] == 5 and line["barcode"] == EXIST_EAN and line["match_code"] == EXIST_CODE
    # preview now reports the saved profile back
    prev = (await owner_client.post("/api/deliveries/preview", data={"supplier": SUP}, files=_up(sheet2))).json()
    assert prev["profile"] is not None and prev["profile"]["col_barcode"] == 3


@pytest.mark.asyncio
async def test_list_and_delete_profiles(owner_client, staff_client):
    sheet = _xlsx([[EXIST_REF, "Закачалка", 3]])
    await owner_client.post("/api/deliveries", files=_up(sheet), data={
        "supplier": SUP, "col_code": 0, "col_name": 1, "col_qty": 2,
        "col_price": -1, "col_barcode": -1, "data_row": 1, "save_profile": "true"})
    lst = (await owner_client.get("/api/deliveries/import-profiles")).json()["profiles"]
    assert len(lst) == 1 and lst[0]["supplier"] == SUP
    pid = lst[0]["id"]
    # staff cannot delete a shared format
    assert (await staff_client.delete(f"/api/deliveries/import-profiles/{pid}")).status_code == 403
    assert (await owner_client.delete(f"/api/deliveries/import-profiles/{pid}")).status_code == 200
    assert (await owner_client.get("/api/deliveries/import-profiles")).json()["profiles"] == []
