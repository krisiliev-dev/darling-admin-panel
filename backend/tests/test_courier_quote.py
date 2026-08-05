# -*- coding: utf-8 -*-
"""Courier quote + speditor reconciliation regressions (2026-07-31).

Both bugs here reached production and both were INVISIBLE to the tests that existed: a quote test that
only checks a fee is RETURNED cannot catch a fee that is wrongly ZERO, and an alias table that happens
to contain the one literal spelling in use looks correct until someone types a variant.
"""
from app.core import courier as courier_mod
from app.routers.orders import _SPEDITOR_ALIASES, _norm_speditor


def _speedy():
    """A Speedy adapter built WITHOUT credentials. `courier_mod.get("speedy")` raises when the keys are
    absent, so a payload-shape test that used it could only ever run on a developer machine — CI has no
    keys, which is correct. We construct the object directly and stub the one credentialed call
    `_calc_payload` makes, so the test asserts the SHAPE and nothing else.
    (My first two attempts at this were false greens: unsetting the env vars locally doesn't help,
    because the module reads ~/.hermes/.env regardless of the environment.)"""
    s = courier_mod.Speedy.__new__(courier_mod.Speedy)
    s._sender = lambda: {"clientId": 1}          # type: ignore[method-assign]
    return s


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
    s = _speedy()
    with_cod = s._calc_payload(_shp(13.50))
    add = (with_cod["service"].get("additionalServices") or {}).get("cod") or {}
    assert add.get("amount") == 13.5
    assert add.get("processingType") == "CASH"
    # …and absent when there is no COD, so a prepaid quote is not inflated
    assert "additionalServices" not in _speedy()._calc_payload(_shp(0.0))["service"]


def test_speedy_payer_is_not_a_substitute_for_the_cod_service():
    """courierServicePayer decides WHO pays the courier fee, not what the COD surcharge is. It flipped
    correctly all along, which is exactly why the missing service block went unnoticed."""
    s = _speedy()
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


# ---------------------------------------------------------------- unprovisioned carriers ---
async def test_unprovisioned_carriers_are_404_not_a_guessed_price(client):
    """The storefront probes the panel before quoting and registers NO shipping rate for a carrier
    that 404s — so a missing courier is simply not offered, which costs nothing. A 200 with a zero or
    estimated fee would make it BOOKABLE at a price nobody can honour, on every order. 404 is the
    correct, safe answer for a carrier we cannot actually price."""
    for carrier in ("boxnow", "sameday", "pigeon", "evropat"):
        for path in (f"/api/public/couriers/{carrier}/offices?city=София",
                     f"/api/public/couriers/{carrier}/quote?dest_type=door&city=София&weight_g=1000"):
            r = await client.get(path, headers={"X-Office-Token": "wrong-on-purpose"})
            # 404 (unknown carrier) or 401/403 (token) — never 200, and never a fee
            assert r.status_code != 200, f"{path} returned 200 for an unprovisioned carrier"


def test_econt_offices_drop_entries_without_a_code(monkeypatch):
    """A null `code` becomes a null office id, which the storefront then fails as
    `office_not_validated` — blaming the customer for a hole in Econt's nomenclature. Unselectable is
    the honest outcome."""
    from app.core import courier as cm
    e = cm.Econt.__new__(cm.Econt)
    monkeypatch.setattr(e, "_post", lambda *a, **k: {"offices": [
        {"code": "5250", "name": "Свищов", "address": {"fullAddress": "ул. 1", "city": {"name": "Свищов"}}},
        {"code": None, "name": "Без код", "address": {"fullAddress": "?", "city": {"name": "Свищов"}}},
        {"code": "", "name": "Празен код", "address": {"fullAddress": "?", "city": {"name": "Свищов"}}},
    ]})
    out = e.offices()
    assert [o["id"] for o in out] == ["5250"]


# ---------------------------------------------------------------- Pigeon Express body ---
def _pigeon():
    """Credential-free Pigeon (same trick as _speedy): construct directly, stub the pickup office
    so _body() never reads env, and never touch the network."""
    p = courier_mod.Pigeon.__new__(courier_mod.Pigeon)
    p._pickup = lambda: {"pickup_type": "office", "pickup_office_id": 90}  # type: ignore[method-assign]
    return p


def _pigeon_shp(cod: float, office_id=None):
    return courier_mod.Shipment(order_ref="q", recipient="О", phone="0000000000",
                                weight_kg=1.0, cod_amount=cod, office_id=office_id,
                                address={"city_id": 759, "street": "Рожен", "street_no": "31"})


def test_pigeon_body_carries_cod_amount_in_quote_and_booking_alike():
    """The Speedy lesson applied ahead of time: ONE body builder serves calculate AND create, so the
    quote and the booking cannot disagree about COD. Verified live 2026-08-05: cod_amount alone
    moves total_price (2.59 → 3.34 EUR, 1kg office→office; response echoes service_codes:
    ["cod_fee"]) — no request-side service code exists."""
    with_cod = _pigeon()._body(_pigeon_shp(50.0, office_id=336))
    assert with_cod["cod_amount"] == 50.0
    assert with_cod["who_pays"] == "receiver"
    without = _pigeon()._body(_pigeon_shp(0.0, office_id=336))
    assert "cod_amount" not in without           # a prepaid quote must not be inflated
    assert without["who_pays"] == "sender"


def test_pigeon_body_packages_carry_required_dims():
    """packages[].length/width/height are REQUIRED (422 validated live 2026-08-05) — a body without
    dims never reaches a price, so the default КАШОН dims must always be present."""
    b = _pigeon()._body(_pigeon_shp(0.0, office_id=336))
    pkg = b["packages"][0]
    assert pkg["weight"] == 1.0
    assert all(pkg.get(k) for k in ("length", "width", "height"))


def test_pigeon_body_delivery_modes():
    """office_id set → delivery_office_id; address → delivery_address{city_id, additional_info}."""
    office = _pigeon()._body(_pigeon_shp(0.0, office_id=336))
    assert office["delivery_type"] == "office" and office["delivery_office_id"] == 336
    door = _pigeon()._body(_pigeon_shp(0.0))
    assert door["delivery_type"] == "address"
    assert door["delivery_address"]["city_id"] == 759
    assert "Рожен" in door["delivery_address"]["additional_info"]


def test_pigeon_without_pickup_office_fails_loudly():
    """No PIGEON_PICKUP_OFFICE_ID → a clear CourierError, never a silently mispriced quote."""
    import pytest as _pytest
    p = courier_mod.Pigeon.__new__(courier_mod.Pigeon)
    p.user = p.password = "x"
    p.base = "https://example.invalid"
    with _pytest.raises(courier_mod.CourierError):
        # _cred() may see a dev ~/.hermes value on a laptop; force-empty via monkeypatching the
        # module-level _cred for the duration of the call
        orig = courier_mod._cred
        courier_mod._cred = lambda k: ""  # type: ignore[assignment]
        try:
            p._pickup()
        finally:
            courier_mod._cred = orig


# ------------------------------------------------------------------------- BoxNow ---
# Surface mapped 2026-08-05 from BOX NOW's official Woo plugin v3.0.0_global (bx_wp_plugin).
# Locker-only, 20 kg hard limit, NO quote API — contract (Анекс) prices via env. Same
# credential-free construction pattern as _speedy()/_pigeon(): __new__ + stubbed _call.

def _boxnow():
    b = courier_mod.BoxNow.__new__(courier_mod.BoxNow)
    b.user = b.password = "x"
    b.base = "https://example.invalid/api/v1"
    b._token_val, b._token_exp = "", 0.0
    return b


def _boxnow_env(monkeypatch, **extra):
    """Process env WINS over ~/.hermes/.env in _cred(), so setenv is enough on any machine."""
    vals = {"BOXNOW_WAREHOUSE_ID": "2", "BOXNOW_PRICE_EUR": "2.20", "BOXNOW_COD_FEE_EUR": "0.40",
            "BOXNOW_SENDER_NAME": "Дарлинг 7", "BOXNOW_SENDER_PHONE": "+359888000000",
            "BOXNOW_SENDER_EMAIL": "orders@darlinghome.bg"}
    vals.update(extra)
    for k, v in vals.items():
        monkeypatch.setenv(k, v)


def _boxnow_shp(cod: float, **kw):
    kw.setdefault("office_id", "APM-77")
    kw.setdefault("weight_kg", 3.0)
    return courier_mod.Shipment(order_ref="SO-1001", recipient="Иван Иванов",
                                phone="+359888111222", cod_amount=cod, **kw)


def test_boxnow_contract_price_folds_cod_and_fails_loud_when_unset(monkeypatch):
    """No quote API: price = Анекс env numbers, COD fee folded in; a MISSING number raises
    (money fails closed — an invented 0.00 locker price would undercharge every order)."""
    import pytest as _pytest
    _boxnow_env(monkeypatch)
    b = _boxnow()
    assert b.calculate(_boxnow_shp(50.0)) == {"price": 2.60, "currency": "EUR", "courier": "boxnow"}
    assert b.calculate(_boxnow_shp(0.0))["price"] == 2.20
    monkeypatch.setenv("BOXNOW_PRICE_EUR", "")
    with _pytest.raises(courier_mod.CourierError, match="BOXNOW_PRICE_EUR"):
        b.calculate(_boxnow_shp(0.0))


def test_boxnow_create_payload_cod_parity_and_mapping(monkeypatch):
    """COD rides in paymentMode+invoiceValue+amountToBeCollected of the SAME body the label is
    booked with (the Speedy quote/booking-parity lesson); origin = OUR warehouse (any-APM id 2),
    destination = the chosen locker; items×parcels with plugin-default compartment 2."""
    _boxnow_env(monkeypatch)
    b = _boxnow()
    seen = {}

    def fake_call(path, payload=None, method=None, raw=False):
        seen.update(path=path, body=payload)
        return {"id": "req-9", "parcels": [{"id": "P111"}, {"id": "P222"}]}

    b._call = fake_call  # type: ignore[method-assign]
    out = b.create_shipment(_boxnow_shp(49.90, parcels=2, email="a@b.bg"), do_write=True)
    assert seen["path"] == "/delivery-requests"
    body = seen["body"]
    assert body["paymentMode"] == "cod"
    assert body["invoiceValue"] == body["amountToBeCollected"] == "49.90"
    assert body["origin"]["locationId"] == "2" and body["origin"]["contactName"] == "Дарлинг 7"
    assert body["destination"]["locationId"] == "APM-77"
    assert len(body["items"]) == 2 and body["items"][0]["compartmentSize"] == 2
    assert out["shipment_id"] == "req-9" and out["tracking_number"] == "P111"
    assert out["parcel_ids"] == ["P111", "P222"] and out["price"] == 2.60
    seen.clear()
    b.create_shipment(_boxnow_shp(0.0), do_write=True)
    assert seen["body"]["paymentMode"] == "prepaid"
    assert seen["body"]["amountToBeCollected"] == "0" == seen["body"]["invoiceValue"]


def test_boxnow_locker_only_weight_limit_and_write_guard(monkeypatch):
    """Locker-only (no door fallback), 20 kg hard limit on BOTH quote and booking, and the
    do_write latch — a BoxNow label is a real-world action."""
    import pytest as _pytest
    _boxnow_env(monkeypatch)
    b = _boxnow()
    with _pytest.raises(courier_mod.CourierError, match="автомат"):
        b.create_shipment(_boxnow_shp(0.0, office_id=None), do_write=True)
    with _pytest.raises(courier_mod.CourierError, match="20"):
        b.calculate(_boxnow_shp(0.0, weight_kg=21))
    with _pytest.raises(courier_mod.CourierError, match="20"):
        b.create_shipment(_boxnow_shp(0.0, weight_kg=20.5), do_write=True)
    with _pytest.raises(courier_mod.CourierError, match="do_write"):
        b.create_shipment(_boxnow_shp(0.0))


def test_boxnow_track_is_honest():
    """The official plugin exposes NO tracking endpoint — track() must say so, not guess one
    (the capture rule). A fabricated endpoint would look like a dead poller: quiet, not broken."""
    import pytest as _pytest
    with _pytest.raises(courier_mod.CourierError, match="capture"):
        _boxnow().track("P111")
