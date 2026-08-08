# -*- coding: utf-8 -*-
"""P5: the API can finally receive a discount column and a per-supplier VAT-inclusive flag, and
remembers both. Until this landed the endpoint had no Form fields for either, so the importer could
never set a discount no matter what the sheet said."""
import io

import openpyxl
import pytest

from app.db import async_session_maker
from app.models import DeliveryLine, SupplierImportProfile
from app.routers import deliveries

SUP = "ТЕСТ-P5"


def _sheet(price=12.0, disc=10):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Код", "Наименование", "Количество", "Цена", "Отстъпка"])
    ws.append(["14153", "Закачалка", 4, price, disc])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _upload(client, supplier=SUP, **form):
    body = {"supplier": supplier, "depot_id": "1", "reference": "P5", "doc_date": "", **form}
    return await client.post(
        "/api/deliveries",
        files={"file": ("s.xlsx", _sheet(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data=body)


async def _line_of(bid):
    async with async_session_maker() as s:
        return (await s.execute(
            deliveries.select(DeliveryLine).where(DeliveryLine.batch_id == bid))).scalars().first()


@pytest.mark.asyncio
async def test_an_explicit_discount_column_is_honoured(owner_client):
    r = await _upload(owner_client, col_code="0", col_name="1", col_qty="2", col_price="3",
                      col_barcode="-1", col_discount="4", data_row="2")
    assert r.status_code == 201, r.text
    ln = await _line_of(r.json()["id"])
    assert ln.printed_price == 12.0 and ln.line_discount_pct == 10.0


@pytest.mark.asyncio
async def test_a_net_priced_supplier_is_recorded_on_the_line(owner_client):
    r = await _upload(owner_client, supplier="ТЕСТ-P5-NET", col_code="0", col_name="1", col_qty="2",
                      col_price="3", col_barcode="-1", col_discount="4", data_row="2",
                      price_includes_vat="false")
    ln = await _line_of(r.json()["id"])
    assert ln.price_includes_vat is False


@pytest.mark.asyncio
async def test_the_profile_remembers_both_and_reuses_them(owner_client):
    """The point of the profile: say it once. A later upload with NO mapping must still get the
    discount column and the VAT answer this supplier was set up with."""
    sup = "ТЕСТ-P5-MEM"
    first = await _upload(owner_client, supplier=sup, col_code="0", col_name="1", col_qty="2",
                          col_price="3", col_barcode="-1", col_discount="4", data_row="2",
                          price_includes_vat="false", save_profile="true")
    assert first.status_code == 201

    async with async_session_maker() as s:
        p = (await s.execute(
            deliveries.select(SupplierImportProfile)
            .where(SupplierImportProfile.supplier == sup))).scalars().first()
        assert p is not None and p.col_discount == 4 and p.price_includes_vat is False

    # no mapping this time — the remembered profile must supply it
    second = await _upload(owner_client, supplier=sup)
    ln = await _line_of(second.json()["id"])
    assert ln.line_discount_pct == 10.0
    assert ln.price_includes_vat is False


@pytest.mark.asyncio
async def test_the_profile_is_exposed_so_the_wizard_can_prefill(owner_client):
    sup = "ТЕСТ-P5-OUT"
    await _upload(owner_client, supplier=sup, col_code="0", col_name="1", col_qty="2",
                  col_price="3", col_barcode="-1", col_discount="4", data_row="2",
                  price_includes_vat="false", save_profile="true")
    rows = (await owner_client.get("/api/deliveries/import-profiles")).json()["profiles"]
    p = next(x for x in rows if x["supplier"] == sup)
    assert p["col_discount"] == 4 and p["price_includes_vat"] is False


@pytest.mark.asyncio
async def test_a_supplier_with_no_discount_column_is_unaffected(owner_client):
    r = await _upload(owner_client, supplier="ТЕСТ-P5-NODISC", col_code="0", col_name="1",
                      col_qty="2", col_price="3", col_barcode="-1", col_discount="-1",
                      data_row="2")
    ln = await _line_of(r.json()["id"])
    assert ln.line_discount_pct == 0.0 and ln.printed_price == 12.0
