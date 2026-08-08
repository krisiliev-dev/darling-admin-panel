# -*- coding: utf-8 -*-
"""P4: the printed values must actually arrive from the sheet. Without this the costing derivation
has no inputs and every line silently falls back to the legacy price column — correct, but inert."""
import pytest

from app.routers import deliveries
from app.services import delivery_parse as DP


def test_a_discount_header_is_detected():
    cols = DP.detect_columns(["Код", "Наименование", "Количество", "Цена", "Отстъпка"])
    assert cols.get("discount") == 4


@pytest.mark.parametrize("header", ["Намаление", "Рабат", "Discount"])
def test_the_other_words_suppliers_use(header):
    cols = DP.detect_columns(["Код", "Наименование", "Количество", "Цена", header])
    assert cols.get("discount") == 4


def test_a_bare_percent_column_is_NOT_read_as_a_discount():
    """„%" is far too common a header to guess from — a VAT column headed % would silently become a
    discount and quietly reduce every cost on the delivery."""
    cols = DP.detect_columns(["Код", "Наименование", "Количество", "Цена", "%"])
    assert cols.get("discount") in (None, -1)


def test_parsed_rows_carry_the_printed_values():
    rows = [
        ["Код", "Наименование", "Количество", "Цена", "Отстъпка"],
        ["A-1", "Чаша", "10", "12.00", "5"],
    ]
    out = deliveries._parse_rows(rows, "ТЕСТ-P4")
    assert len(out) == 1
    ln = out[0]
    assert ln["printed_price"] == 12.0
    assert ln["line_discount_pct"] == 5.0
    assert ln["price_includes_vat"] is True          # the default, matching barsy_price_mode = 1
    assert ln["price"] == 12.0                       # legacy column unchanged


def test_a_sheet_without_a_discount_column_still_parses():
    rows = [["Код", "Наименование", "Количество", "Цена"], ["A-1", "Чаша", "10", "12.00"]]
    ln = deliveries._parse_rows(rows, "ТЕСТ-P4")[0]
    assert ln["line_discount_pct"] == 0.0 and ln["printed_price"] == 12.0


def test_a_net_priced_supplier_is_recorded_as_such():
    rows = [["Код", "Наименование", "Количество", "Цена"], ["A-1", "Чаша", "10", "10.00"]]
    ln = deliveries._parse_rows(rows, "ТЕСТ-P4", includes_vat=False)[0]
    assert ln["price_includes_vat"] is False


@pytest.mark.asyncio
async def test_upload_persists_printed_price_and_discount(owner_client):
    import io

    import openpyxl

    from app.db import async_session_maker
    from app.models import DeliveryLine

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Код", "Наименование", "Количество", "Цена", "Отстъпка"])
    ws.append(["14153", "Закачалка", 4, 12.5, 10])
    buf = io.BytesIO()
    wb.save(buf)

    r = await owner_client.post(
        "/api/deliveries",
        files={"file": ("sheet.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"supplier": "ТЕСТ-P4", "depot_id": "1", "reference": "P4", "doc_date": ""})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    async with async_session_maker() as s:
        ln = (await s.execute(
            deliveries.select(DeliveryLine).where(DeliveryLine.batch_id == bid))).scalars().first()
        assert ln.printed_price == 12.5
        assert ln.line_discount_pct == 10.0
        assert ln.price_includes_vat is True
        assert ln.price == 12.5           # legacy column still populated, nothing in flight moves
