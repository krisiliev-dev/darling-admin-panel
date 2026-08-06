# -*- coding: utf-8 -*-
"""Sameday adapter regressions (2026-08-06).

Nothing here has touched Sameday's API: the credentials are not issued yet, so every test
stubs the transport and asserts the SHAPE and the ARITHMETIC. That is deliberate — the
things that can be proved today are exactly the ones that cost money if they are wrong,
and the things that cannot (does their host really answer in BGN, which service id is 24)
are listed as VERIFY-LIVE at the top of the adapter instead of being asserted here against
a guess.

The two that would hurt most:
  * BGN/EUR. Their BG instance quotes in BGN and our stack is EUR. Reading one as the other
    prices every parcel at about half.
  * clientInternalReference. Their own FAQ says to add a suffix and retry when it collides.
    Doing that books a SECOND waybill for one order — two labels, two COD collections.
"""
import pytest

from app.core import courier as courier_mod

Sameday = courier_mod.Sameday
CourierError = courier_mod.CourierError


def _sd():
    """A Sameday adapter with no credentials and no network. Built with __new__ because
    __init__ reads ~/.hermes/.env, which exists on the laptop and not on CI — a test that
    behaves differently in those two places is not a test."""
    s = Sameday.__new__(Sameday)
    s.user = s.password = ""
    s.base = "https://api.sameday.bg"
    s._tok, s._tok_exp, s._svc, s._pp = None, 0.0, None, None
    s._svc = [{"id": 7, "serviceCode": "24", "name": "Sameday 24H"},
              {"id": 9, "serviceCode": "LN", "name": "LockerNextDay"}]
    s._pp = 4242
    return s


def _shp(cod=0.0, office_id=None, **kw):
    base = {"order_ref": "WC-1001", "recipient": "Иван Петров", "phone": "0893361670",
            "email": "ivan@example.com", "weight_kg": 1.5, "cod_amount": cod,
            "address": {"city": "Свищов", "county": "Велико Търново",
                        "street": "ул. Дунав", "street_no": "3", "postcode": "5250"}}
    base.update(kw)
    if office_id:
        base["office_id"] = office_id
    return courier_mod.Shipment(**base)


# ----------------------------------------------------------------- the currency trap
def test_the_peg_is_the_statutory_rate():
    assert str(Sameday.BGN_PER_EUR) == "1.95583"


def test_eur_to_bgn_and_back():
    assert Sameday._to_bgn(10) == 19.56
    assert Sameday._to_eur(19.56) == 10.0
    assert Sameday._to_bgn("0.51") == 1.0
    assert Sameday._to_eur(1) == 0.51


def test_a_bgn_quote_is_converted_not_taken_at_face_value():
    """The whole point. 19.56 BGN is 10 EUR, not 19.56 EUR."""
    assert Sameday._price_eur(19.56, "BGN") == 10.0
    assert Sameday._price_eur(19.56, "bgn") == 10.0


def test_a_eur_quote_passes_through():
    assert Sameday._price_eur(4.99, "EUR") == 4.99


@pytest.mark.parametrize("currency", ["RON", "HUF", "USD", "", None, "bgnn"])
def test_an_unexpected_currency_is_refused_not_assumed(currency):
    with pytest.raises(CourierError) as e:
        Sameday._price_eur(19.56, currency)
    assert "валута" in str(e.value)


def test_calculate_converts_the_quote(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: {"amount": 7.82, "currency": "BGN", "time": "24h"})
    out = sd.calculate(_shp())
    assert out == {"price": 4.0, "currency": "EUR", "eta": "24h"}


def test_calculate_refuses_a_response_with_no_amount(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: {"currency": "BGN"})
    with pytest.raises(CourierError):
        sd.calculate(_shp())


# -------------------------------------------------------------------- the COD trap
def test_cod_rides_in_the_quote_and_the_booking_identically():
    """The Speedy incident: a quote without COD and a booking with it differ by the COD fee,
    which is a silent per-order loss on the 55-60% of orders that are COD. One body builder
    means the two cannot disagree."""
    sd, shp = _sd(), _shp(cod=59.90)
    quote = sd._body(shp)
    booking = sd._body(shp, reference="WC-1001")
    assert quote["cashOnDelivery"] == booking["cashOnDelivery"] == Sameday._to_bgn(59.90)
    assert quote["service"] == booking["service"]
    assert quote["packageWeight"] == booking["packageWeight"] == 1.5


def test_cod_is_sent_in_bgn_not_eur():
    body = _sd()._body(_shp(cod=100.0))
    assert body["cashOnDelivery"] == 195.58        # not 100
    assert body["currency"] == "BGN"


def test_a_prepaid_order_sends_zero_cod():
    assert _sd()._body(_shp(cod=0))["cashOnDelivery"] == 0


def test_only_the_booking_names_the_cod_collector():
    """cashOnDeliveryReturns is an AWB field; estimate-cost does not take it."""
    sd, shp = _sd(), _shp(cod=10)
    assert "cashOnDeliveryReturns" not in sd._body(shp)
    assert sd._body(shp, reference="X")["cashOnDeliveryReturns"] == Sameday.COD_COLLECTOR_CLIENT


# ------------------------------------------------------- the internal reference trap
def test_the_reference_is_the_order_ref_verbatim():
    body = _sd()._body(_shp(), reference="WC-1001")
    assert body["clientInternalReference"] == "WC-1001"


def test_create_shipment_refuses_without_an_order_ref():
    with pytest.raises(CourierError) as e:
        _sd().create_shipment(_shp(order_ref=""), do_write=True)
    assert "clientInternalReference" in str(e.value)


def test_a_duplicate_reference_says_already_booked_not_try_again(monkeypatch):
    """Their FAQ says to append a suffix. We must not: that books a second waybill for one
    order. The error has to send a human to look the existing one up."""
    import io
    import urllib.error

    sd = _sd()
    sd._tok = "t"

    def boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://api.sameday.bg/api/awb", 422, "Unprocessable", {},
            io.BytesIO(b'{"errors":{"clientInternalReference":"is not unique"}}'))

    monkeypatch.setattr(courier_mod.urllib.request, "urlopen", boom)
    with pytest.raises(CourierError) as e:
        sd._call("/api/awb", {"clientInternalReference": "WC-1"})
    msg = str(e.value)
    assert "ВЕЧЕ" in msg
    assert "суфикс" in msg


# ------------------------------------------------------------------ write protection
def test_create_shipment_without_do_write_makes_no_call(monkeypatch):
    sd = _sd()

    def forbidden(*a, **k):
        raise AssertionError("a dry run must not touch the API")

    monkeypatch.setattr(sd, "_call", forbidden)
    out = sd.create_shipment(_shp(cod=20), do_write=False)
    assert out["dry_run"] is True
    assert out["payload"]["clientInternalReference"] == "WC-1001"


def test_create_shipment_refuses_a_response_without_an_awb(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: {"awbCost": 7.82})
    with pytest.raises(CourierError):
        sd.create_shipment(_shp(), do_write=True)


def test_create_shipment_returns_eur_and_the_awb(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: {
        "awbNumber": "1BG123456", "awbCost": 7.82,
        "parcels": [{"awbNumber": "1BG123456-1"}]})
    out = sd.create_shipment(_shp(cod=30), do_write=True)
    assert out["shipment_id"] == "1BG123456"
    assert out["tracking_number"] == "1BG123456"
    assert out["currency"] == "EUR"
    assert out["price"] == 4.0                      # 7.82 BGN
    assert out["cod_amount"] == 30                  # our side stays EUR
    assert out["parcel_ids"] == ["1BG123456-1"]


# ---------------------------------------------------------------- recipient validation
def test_phone_is_mandatory():
    """Top of Sameday's own error list: awbRecipient.phone is missing."""
    with pytest.raises(CourierError) as e:
        _sd()._recipient(_shp(phone=""))
    assert "телефон" in str(e.value)


def test_a_door_delivery_needs_a_city():
    shp = _shp()
    shp.address = {"street": "ул. Дунав", "street_no": "3"}
    with pytest.raises(CourierError):
        _sd()._recipient(shp)


def test_a_door_delivery_needs_a_street():
    shp = _shp()
    shp.address = {"city": "Свищов", "county": "Велико Търново"}
    with pytest.raises(CourierError):
        _sd()._recipient(shp)


def test_the_county_is_looked_up_when_the_order_does_not_carry_one(monkeypatch):
    sd = _sd()
    shp = _shp()
    shp.address = {"city": "Свищов", "street": "ул. Дунав", "street_no": "3"}
    monkeypatch.setattr(sd, "_county_for", lambda city: "Велико Търново")
    assert sd._recipient(shp)["countyString"] == "Велико Търново"


def test_an_unresolvable_county_fails_loud(monkeypatch):
    sd = _sd()
    shp = _shp()
    shp.address = {"city": "Несъществуващо", "street": "ул. Х", "street_no": "1"}
    monkeypatch.setattr(sd, "_county_for", lambda city: None)
    with pytest.raises(CourierError):
        sd._recipient(shp)


def test_recipient_is_an_individual_with_string_place_names():
    r = _sd()._recipient(_shp())
    assert r["personType"] == Sameday.PERSON_INDIVIDUAL
    assert r["cityString"] == "Свищов"
    assert r["countyString"] == "Велико Търново"
    assert r["email"] == "ivan@example.com"
    assert r["phoneNumber"] == "0893361670"


# --------------------------------------------------------------------- service routing
def test_a_door_order_uses_service_24_and_no_locker_field():
    body = _sd()._body(_shp(), reference="R")
    assert body["service"] == 7
    assert "lockerLastMile" not in body


def test_a_locker_order_uses_service_ln_and_locker_last_mile():
    body = _sd()._body(_shp(office_id="88123"), reference="R")
    assert body["service"] == 9
    assert body["lockerLastMile"] == "88123"


def test_a_missing_service_code_names_what_the_contract_does_have():
    sd = _sd()
    sd._svc = [{"id": 1, "serviceCode": "XB"}]
    with pytest.raises(CourierError) as e:
        sd._service_id("LN")
    assert "XB" in str(e.value)


def test_the_default_pickup_point_is_read_from_the_account(monkeypatch):
    sd = _sd()
    sd._pp = None
    monkeypatch.setattr(courier_mod, "_cred", lambda k: "")
    monkeypatch.setattr(sd, "_pages", lambda *a, **k: [
        {"id": 1, "defaultPickupPoint": False}, {"id": 2, "defaultPickupPoint": True}])
    assert sd._pickup_point() == 2


def test_no_default_pickup_point_fails_loud(monkeypatch):
    sd = _sd()
    sd._pp = None
    monkeypatch.setattr(courier_mod, "_cred", lambda k: "")
    monkeypatch.setattr(sd, "_pages", lambda *a, **k: [{"id": 1, "defaultPickupPoint": False}])
    with pytest.raises(CourierError):
        sd._pickup_point()


# ------------------------------------------------------------------------ form encoding
def test_nested_bodies_encode_the_way_php_does():
    """Their SDK posts application/x-www-form-urlencoded built by http_build_query, so a
    nested body has to flatten to awbRecipient[name] and parcels[0][weight] or their
    validator sees an empty recipient."""
    pairs = dict(courier_mod._form_pairs({
        "service": 7,
        "awbRecipient": {"name": "Иван", "phoneNumber": "0888"},
        "parcels": [{"weight": 1.5, "width": 20}],
        "serviceTaxes": [],
    }))
    assert pairs["service"] == 7
    assert pairs["awbRecipient[name]"] == "Иван"
    assert pairs["awbRecipient[phoneNumber]"] == "0888"
    assert pairs["parcels[0][weight]"] == 1.5
    assert pairs["parcels[0][width]"] == 20


def test_nulls_are_dropped_and_booleans_become_numbers():
    pairs = dict(courier_mod._form_pairs({"a": None, "b": True, "c": False, "d": 0}))
    assert "a" not in pairs
    assert pairs["b"] == 1 and pairs["c"] == 0 and pairs["d"] == 0


# ------------------------------------------------------------------------------- misc
def test_the_api_base_is_the_host_only():
    """Every endpoint already starts with /api; a base ending in /api gives /api/api/..."""
    assert courier_mod.REGISTRY["sameday"]["base"] == "https://api.sameday.bg"


def test_label_format_is_a4_or_a6():
    with pytest.raises(CourierError):
        _sd().label_pdf("1BG1", fmt="A5")


def test_label_must_actually_be_a_pdf(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: b"<html>not a pdf")
    with pytest.raises(CourierError):
        sd.label_pdf("1BG1")


def test_track_reports_three_things_not_a_boolean(monkeypatch):
    sd = _sd()
    monkeypatch.setattr(sd, "_call", lambda *a, **k: {
        "delivered": False,
        "expeditionStatus": {"status": "IN_TRANSIT", "statusLabel": "В транзит",
                             "statusDate": "2026-08-06 10:00:00"},
        "expeditionHistory": [{"statusLabel": "Приета", "statusDate": "2026-08-05 18:00:00"}]})
    out = sd.track("1BG1")
    assert out["delivered"] is False
    assert out["status_bg"] == "В транзит"
    assert out["events"][0]["text"] == "Приета"


def test_an_unimplemented_adapter_fails_in_bulgarian_not_with_a_traceback():
    """Evropat has no API mapped. With credentials set, the console used to get a bare
    NotImplementedError; an operator cannot act on that."""
    ev = courier_mod.Evropat.__new__(courier_mod.Evropat)
    ev.name = "evropat"
    with pytest.raises(CourierError) as e:
        ev.calculate(_shp())
    assert "Европът" in str(e.value)
