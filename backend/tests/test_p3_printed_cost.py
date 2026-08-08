# -*- coding: utf-8 -*-
"""P3: the printed invoice values, and what actually reaches Barsy.

The trap this slice exists to avoid: barsy_price_mode = 1 declares our prices VAT-INCLUSIVE and
Barsy strips the VAT itself. Sending a derived NET price under that contract strips it twice and
understates cost by ~17%, with nothing on screen to say so."""
from decimal import Decimal

import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine
from app.routers import deliveries
from app.services import costing as C


def _b(**kw):
    return DeliveryBatch(supplier="ТЕСТ-P3", depot_id=1, reference="P3", doc_date="",
                         source="file", status="ready", created_by="t@t",
                         doc_discount_pct=kw.pop("doc_discount_pct", 0.0),
                         fx_rate=kw.pop("fx_rate", 1.0), **kw)


def _ln(**kw):
    return DeliveryLine(batch_id=1, name="Артикул", qty=kw.pop("qty", 1.0),
                        price=kw.pop("price", 0.0),
                        printed_price=kw.pop("printed_price", 0.0),
                        price_includes_vat=kw.pop("price_includes_vat", True),
                        vat_group_id=kw.pop("vat_group_id", 2),
                        line_discount_pct=kw.pop("line_discount_pct", 0.0),
                        scanned_qty=kw.pop("scanned_qty", 0.0), status="pending", **kw)


# ── the double-strip trap ────────────────────────────────────────────────────
def test_barsy_gets_a_gross_price_not_a_net_one():
    """Barsy strips VAT itself under price_mode=1. A printed 12.00 inc-VAT must go back as 12.00,
    NOT as the 10.00 net — sending net would have Barsy strip it again down to 8.33."""
    got = C.barsy_unit_price("12.00", includes_vat=True, vat_group_id=2)
    assert got == Decimal("12.0000")
    assert C.net_unit_price("12.00", includes_vat=True, vat_group_id=2) == Decimal("10.0000")


def test_a_net_printed_price_is_grossed_up_for_barsy():
    """A supplier sheet quoting net must still reach Barsy gross, or Barsy's strip understates it."""
    assert C.barsy_unit_price("10.00", includes_vat=False, vat_group_id=2) == Decimal("12.0000")


def test_discounts_and_fx_now_actually_reach_barsy():
    """The real improvement: these were previously ignored — the printed price went through raw."""
    got = C.barsy_unit_price("12.00", includes_vat=True, vat_group_id=2,
                             line_discount_pct=10, doc_discount_pct=5)
    assert got == Decimal("10.2600")          # 12 × 0.9 × 0.95, VAT retained


def test_price_mode_zero_would_send_net():
    """If price_mode is ever flipped, the same helper sends net instead — one switch, not a rewrite."""
    assert C.barsy_unit_price("12.00", includes_vat=True, vat_group_id=2,
                              barsy_expects_vat_inclusive=False) == Decimal("10.0000")


# ── backward compatibility ───────────────────────────────────────────────────
def test_a_line_with_no_printed_price_uses_the_legacy_column_untouched():
    """A delivery already in flight must not change value under the operator."""
    b, ln = _b(), _ln(price=7.77, printed_price=0.0)
    assert deliveries._unit_price_for_barsy(ln, b) == 7.77
    assert deliveries._net_unit_cost(ln, b) == 7.77


def test_a_printed_line_derives_instead():
    b, ln = _b(doc_discount_pct=10.0), _ln(price=99.0, printed_price=12.0)
    assert deliveries._unit_price_for_barsy(ln, b) == pytest.approx(10.80)   # 12 × 0.9, gross
    assert deliveries._net_unit_cost(ln, b) == pytest.approx(9.00)           # …and 9.00 net


# ── reconciliation against the invoice ───────────────────────────────────────
def test_reconciliation_is_unavailable_without_an_invoice_total():
    b = _b()
    b.invoice_total = 0.0
    assert deliveries._reconcile_invoice(b, [_ln(printed_price=12.0)])["available"] is False


def test_a_clean_invoice_reconciles():
    b = _b()
    b.invoice_total = 120.0
    lines = [_ln(qty=10.0, scanned_qty=10.0, printed_price=12.0)]
    r = deliveries._reconcile_invoice(b, lines)
    assert r["available"] is True and r["reconciles"] is True
    assert r["net"] == pytest.approx(100.0) and r["gross"] == pytest.approx(120.0)


def test_a_short_delivery_reconciles_against_what_ARRIVED():
    """Otherwise the residual just restates the shortfall we already recorded."""
    b = _b()
    b.invoice_total = 120.0
    lines = [_ln(qty=10.0, scanned_qty=8.0, printed_price=12.0)]
    r = deliveries._reconcile_invoice(b, lines)
    assert r["gross"] == pytest.approx(96.0)          # 8 arrived, not 10 billed
    assert r["reconciles"] is False
    assert r["residual"] == pytest.approx(-24.0)


def test_unpriced_lines_are_counted_not_silently_dropped():
    b = _b()
    b.invoice_total = 120.0
    lines = [_ln(qty=10.0, scanned_qty=10.0, printed_price=12.0), _ln(qty=1.0, price=5.0)]
    assert deliveries._reconcile_invoice(b, lines)["unpriced_lines"] == 1


# ── the endpoint ─────────────────────────────────────────────────────────────
async def _batch_in_db(printed=12.0, qty=10.0):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier="ТЕСТ-P3", depot_id=1, reference="P3-DB", doc_date="",
                          source="file", status="ready", created_by="t@t")
        s.add(b)
        await s.flush()
        s.add(DeliveryLine(batch_id=b.id, name="Артикул", qty=qty, scanned_qty=qty,
                           price=0.0, printed_price=printed, price_includes_vat=True,
                           vat_group_id=2, is_new=False, match_code="14153", article_id=999,
                           status="confirmed"))
        await s.commit()
        return b.id


@pytest.mark.asyncio
async def test_setting_the_invoice_returns_the_reconciliation(owner_client):
    bid = await _batch_in_db()
    r = await owner_client.post(f"/api/deliveries/{bid}/invoice",
                                json={"invoice_total": 120.0, "currency": "EUR", "fx_rate": 1.0})
    assert r.status_code == 200, r.text
    rec = r.json()["reconciliation"]
    assert rec["reconciles"] is True and rec["residual"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_wrong_invoice_total_shows_the_residual(owner_client):
    bid = await _batch_in_db()
    r = await owner_client.post(f"/api/deliveries/{bid}/invoice", json={"invoice_total": 130.0})
    rec = r.json()["reconciliation"]
    assert rec["reconciles"] is False and rec["residual"] == pytest.approx(-10.0)


@pytest.mark.asyncio
async def test_the_invoice_endpoint_validates(owner_client):
    bid = await _batch_in_db()
    for body in ({"currency": "USD"}, {"fx_rate": 0}, {"doc_discount_pct": 150},
                 {"invoice_total": -5}):
        r = await owner_client.post(f"/api/deliveries/{bid}/invoice", json=body)
        assert r.status_code == 400, body
