# -*- coding: utf-8 -*-
"""Zero-price go-live guard — the money invariant (owner, 2026-08-02): nothing priced 0
reaches website or till. Three states; BLOCKED yields an UNUSABLE payload on purpose."""
import pytest

from app import pricing_guard as pg


@pytest.mark.parametrize("price,expected", [
    (12.5, pg.OK), ("12.50", pg.OK), ("12,50", pg.OK), (1, pg.OK), ("0.01", pg.OK),
    (0, pg.FORCED_INACTIVE), ("0", pg.FORCED_INACTIVE), ("0,00", pg.FORCED_INACTIVE),
    (-3, pg.FORCED_INACTIVE),
    (None, pg.BLOCKED), ("", pg.BLOCKED), ("   ", pg.BLOCKED), ("н/д", pg.BLOCKED),
    ("12,50 лв", pg.BLOCKED),   # units in the field = unreadable, NOT parsed permissively
])
def test_gate_states(price, expected):
    assert pg.gate(price)["state"] == expected


def test_forced_inactive_pulls_article_from_till_AND_web():
    """Barsy mapping (owner, 28.07): is_for_sale=0 is the only state that blocks BOTH
    channels; is_public=0 alone would leave the product sellable in-store."""
    art = {"name": "Чаша", "price": 0, "is_for_sale": 1, "is_public": 1}
    out, verdict = pg.apply_to_article(art, 0)
    assert verdict["state"] == pg.FORCED_INACTIVE
    assert out["is_for_sale"] == 0 and out["is_public"] == 0
    assert art["is_for_sale"] == 1          # a COPY was modified, never the caller's dict


def test_ok_passes_payload_untouched():
    art = {"name": "Чаша", "is_for_sale": 1}
    out, verdict = pg.apply_to_article(art, "4,90")
    assert verdict["state"] == pg.OK and out is art


def test_blocked_payload_is_unusable_not_falsy_dict():
    """Fail closed on money: an unreadable price returns payload None — a caller that
    skips the verdict check crashes on push instead of pushing a guessed article."""
    out, verdict = pg.apply_to_article({"name": "Чаша"}, None)
    assert verdict["state"] == pg.BLOCKED
    assert out is None
