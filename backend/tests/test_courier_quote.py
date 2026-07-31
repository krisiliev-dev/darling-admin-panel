# -*- coding: utf-8 -*-
"""Courier quote + speditor reconciliation regressions (2026-07-31).

Both bugs here reached production and both were INVISIBLE to the tests that existed: a quote test that
only checks a fee is RETURNED cannot catch a fee that is wrongly ZERO, and an alias table that happens
to contain the one literal spelling in use looks correct until someone types a variant.
"""
from app.core import courier as courier_mod
from app.routers.orders import _SPEDITOR_ALIASES, _norm_speditor


def _shp(cod: float):
    # site_id is supplied so _calc_payload never calls _resolve_site() — that is a LIVE Speedy
    # nomenclature lookup, and a unit test must not depend on credentials or the network. (It did on
    # the first attempt: green locally where creds exist, red on CI where they do not.)
    return courier_mod.Shipment(order_ref="q", recipient="О", phone="0000000000",
                                weight_kg=1.0, cod_amount=cod,
                                address={"city": "Свищов", "postcode": "", "street": "",
                                         "site_id": 68789})


# ---------------------------------------------------------------- Speedy COD surcharge ---
def test_speedy_calc_payload_carries_the_cod_service():
    """THE regression. _service() (shipment) attached the COD service and _calc_payload() (quote) did
    not, so Speedy priced a parcel with no наложен платеж and returned the same total either way:
    cod_fee 0.00 on every quote, while the real label was booked WITH the surcharge and we paid it.
    Quote path and booking path must agree — on 55-60% of orders the gap is a silent per-order loss."""
    s = courier_mod.get("speedy")
    with_cod = s._calc_payload(_shp(13.50))
    add = (with_cod["service"].get("additionalServices") or {}).get("cod") or {}
    assert add.get("amount") == 13.5
    assert add.get("processingType") == "CASH"
    # …and absent when there is no COD, so a prepaid quote is not inflated
    assert "additionalServices" not in courier_mod.get("speedy")._calc_payload(_shp(0.0))["service"]


def test_speedy_payer_is_not_a_substitute_for_the_cod_service():
    """courierServicePayer decides WHO pays the courier fee, not what the COD surcharge is. It flipped
    correctly all along, which is exactly why the missing service block went unnoticed."""
    s = courier_mod.get("speedy")
    assert s._calc_payload(_shp(0.0))["payment"]["courierServicePayer"] == "SENDER"
    assert s._calc_payload(_shp(9.99))["payment"]["courierServicePayer"] == "RECIPIENT"


# ---------------------------------------------------------------- speditor name folding ---
def test_speditor_names_fold_across_real_barsy_spellings():
    """Barsy stores "BOX NOW" while our key is `boxnow`; an exact compare misses on the space and
    yields a waybill with NO courier attached, silently. Hermes's mapper broke on precisely this."""
    for raw, expect in [("BOX NOW", "boxnow"), ("Box  Now", "boxnow"), ("box-now", "boxnow"),
                        ("Еконт", "econt"), ("ЕКОНТ", "econt"), ("Спиди", "speedy"),
                        ("Speedy", "speedy"), ("Sameday", "sameday"),
                        ("Европът", "evropat"), ("Pigeon Express", "pigeon")]:
        assert _SPEDITOR_ALIASES.get(_norm_speditor(raw)) == expect, raw


def test_barsy_pseudo_speditors_stay_unmapped():
    """"Поща" and "От офис" are Barsy's own pseudo-speditors, not couriers we ship with. They must NOT
    fold onto a real courier — a wrong speditor_id on a sale is worse than none."""
    for raw in ("Поща", "От офис", "", "   "):
        assert _SPEDITOR_ALIASES.get(_norm_speditor(raw)) is None, raw
