# -*- coding: utf-8 -*-
"""Cost-as-printed arithmetic. This is the code that decides what a thing cost, so it is tested
before anything is wired to it — a quiet error here corrupts stock valuation and margins with
nothing on screen to suggest anything is wrong."""
from decimal import Decimal

import pytest

from app.services import costing as C


# ── the VAT toggle Barsy warns about ─────────────────────────────────────────
def test_vat_inclusive_price_is_ungrossed():
    """Barsy's own tooltip: tick С ДДС or себестойността computes wrong. 12.00 inc. 20% is 10.00."""
    assert C.net_unit_price("12.00", includes_vat=True, vat_group_id=2) == Decimal("10.0000")


def test_vat_exclusive_price_is_left_alone():
    assert C.net_unit_price("10.00", includes_vat=False, vat_group_id=2) == Decimal("10.0000")


def test_getting_the_toggle_wrong_is_a_twenty_percent_error():
    """The whole reason it is stored per line rather than assumed."""
    right = C.net_unit_price("12.00", includes_vat=True, vat_group_id=2)
    wrong = C.net_unit_price("12.00", includes_vat=False, vat_group_id=2)
    assert wrong - right == Decimal("2.0000")


@pytest.mark.parametrize("group,expected", [
    (1, "10.0000"),      # BG 0% (А)
    (2, "8.3333"),       # BG 20% (Б)
    (3, "8.3333"),       # BG 20% (В) — same rate, different reporting
    (4, "9.1743"),       # BG 9% (Г)
    (12, "9.3458"),      # BG 7%
    (100, "10.0000"),    # BG 0% (Без ДДС)
])
def test_every_barsy_vat_group(group, expected):
    assert C.net_unit_price("10.00", includes_vat=True, vat_group_id=group) == Decimal(expected)


def test_an_unknown_vat_group_does_not_invent_tax():
    """Inventing 20% would silently understate cost; a zero is at least visible."""
    assert C.vat_rate(9999) == Decimal("0")
    assert C.vat_rate(None) == Decimal("0")


# ── discounts, both levels ───────────────────────────────────────────────────
def test_line_discount():
    assert C.net_unit_price("100", includes_vat=False, line_discount_pct=10) == Decimal("90.0000")


def test_document_discount():
    assert C.net_unit_price("100", includes_vat=False, doc_discount_pct=5) == Decimal("95.0000")


def test_the_two_discounts_compound_they_do_not_add():
    """10% then 5% is 85.50, not 85.00 — the classic off-by-half-a-lev."""
    assert C.net_unit_price("100", includes_vat=False,
                            line_discount_pct=10, doc_discount_pct=5) == Decimal("85.5000")


def test_vat_is_stripped_before_discounts_are_applied():
    """VAT is charged on the discounted net, so un-grossing must come first. 120 inc-20% with 10%
    off is 90.00; discounting first and then un-grossing gives the same here only because both are
    multiplications — this pins the ORDER so a future edit cannot quietly reverse it."""
    assert C.net_unit_price("120", includes_vat=True, vat_group_id=2,
                            line_discount_pct=10) == Decimal("90.0000")


# ── currency ─────────────────────────────────────────────────────────────────
def test_fx_rate_converts():
    """BGN at the fixed 1.95583 → EUR is the live case for the changeover."""
    got = C.net_unit_price("19.5583", includes_vat=False, fx_rate="0.511292")
    assert got == Decimal("10.0000")


def test_a_missing_rate_is_one_not_zero():
    """A blank rate must not annihilate the price."""
    assert C.net_unit_price("10", includes_vat=False, fx_rate="") == Decimal("10.0000")


# ── junk in, visible zero out ────────────────────────────────────────────────
@pytest.mark.parametrize("junk", ["", None, "n/a", "—", "abc"])
def test_a_malformed_price_lands_as_zero_rather_than_exploding(junk):
    assert C.net_unit_price(junk, includes_vat=False) == Decimal("0")


def test_bulgarian_decimal_comma_and_spaces():
    """Sheets arrive with '1 234,56'."""
    assert C.dec("1 234,56") == Decimal("1234.56")


# ── rounding: once, at the total ─────────────────────────────────────────────
def test_the_total_rounds_once_not_per_unit():
    """0.3333 × 3 is 0.9999 → 1.00. Rounding the unit to 0.33 first would give 0.99, and on a pallet
    that difference is real money."""
    assert C.line_net_total(3, "0.3333", includes_vat=False) == Decimal("1.00")


def test_unit_price_keeps_four_places():
    """A 0.005 error on a unit cost becomes 0.36 across a case of 72."""
    assert C.net_unit_price("1", includes_vat=True, vat_group_id=2) == Decimal("0.8333")


# ── reconciliation + the residual ────────────────────────────────────────────
def _line(qty, price, **kw):
    return {"qty": qty, "printed_price": price, "includes_vat": False, "vat_group_id": 2, **kw}


def test_a_clean_invoice_reconciles():
    r = C.reconcile([_line(2, "10.00"), _line(3, "5.00")], "42.00")
    assert r["net"] == Decimal("35.00")
    assert r["vat"] == Decimal("7.00")
    assert r["gross"] == Decimal("42.00")
    assert r["residual"] == Decimal("0.00") and r["reconciles"] is True


def test_a_rounding_residual_is_tolerated_and_reported():
    """A stotinka of drift is arithmetic, not a dispute — but the number is kept either way."""
    r = C.reconcile([_line(3, "3.333")], "12.00")
    assert r["reconciles"] is True
    assert abs(r["residual"]) <= Decimal("0.01")


def test_a_real_discrepancy_does_not_reconcile():
    """The point of the residual: telling a rounding artefact from somebody's mistake."""
    r = C.reconcile([_line(2, "10.00")], "25.00")
    assert r["reconciles"] is False
    assert r["residual"] == Decimal("-1.00")     # derived 24.00 vs printed 25.00


def test_reconcile_against_a_net_total():
    r = C.reconcile([_line(2, "10.00")], "20.00", with_vat=False)
    assert r["reconciles"] is True and r["residual"] == Decimal("0.00")


def test_reconcile_handles_a_vat_inclusive_invoice():
    """The realistic shape: prices printed with VAT, doc-level discount, total gross."""
    lines = [_line(10, "12.00", includes_vat=True, doc_discount_pct=10)]
    r = C.reconcile(lines, "108.00")
    assert r["net"] == Decimal("90.00")
    assert r["vat"] == Decimal("18.00")
    assert r["reconciles"] is True
