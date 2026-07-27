# -*- coding: utf-8 -*-
"""Public token-authed courier office lookup (/api/public/couriers/{key}/offices) — the ONE external
entry point, for the storefront's Worker. Courier creds stay server-side; the caller presents
X-Office-Token. Disabled (403) unless a token is configured; no panel login required with the token."""
from types import SimpleNamespace

import pytest

from app.routers import public_couriers as pc

_OFFICES = [{"id": "42", "name": "Офис Люлин", "address": "бул. Царица Йоанна 15", "city": "София"}]


class _FakeCourier:
    def offices(self, city=None):
        return _OFFICES


@pytest.fixture(autouse=True)
def _clear_office_cache():
    pc._OFFICE_CACHE.clear()
    yield
    pc._OFFICE_CACHE.clear()


@pytest.fixture
def _fake_courier(monkeypatch):
    monkeypatch.setattr(pc.courier_mod, "get", lambda k: _FakeCourier())


def _set_token(monkeypatch, token):
    monkeypatch.setattr(pc, "get_settings", lambda: SimpleNamespace(office_lookup_token=token))


async def test_disabled_when_no_token(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "")                      # no secret configured → feature off
    r = await client.get("/api/public/couriers/speedy/offices", headers={"x-office-token": "anything"})
    assert r.status_code == 403


async def test_rejects_wrong_token(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/speedy/offices", headers={"x-office-token": "nope"})
    assert r.status_code == 401


async def test_rejects_missing_token(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/speedy/offices")
    assert r.status_code == 401


async def test_valid_token_returns_offices(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/speedy/offices?city=София",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["courier"] == "speedy" and body["count"] == 1
    assert body["offices"][0] == {"id": "42", "name": "Офис Люлин",
                                  "address": "бул. Царица Йоанна 15", "city": "София"}


async def test_city_filter_matches_cyrillic(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    # the ?city=София→0 bug: filter must match the office's city (Cyrillic, case/space-insensitive)
    r = await client.get("/api/public/couriers/econt/offices?city=софия",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200 and r.json()["count"] == 1


async def test_city_filter_excludes_other_cities(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/offices?city=Пловдив",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200 and r.json()["count"] == 0


async def test_unknown_courier_404(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/nope/offices", headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 404


async def test_token_holder_needs_no_panel_login(client, _fake_courier, monkeypatch):
    # the whole point: `client` is UNAUTHENTICATED (no panel cookie) yet succeeds with the token
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/offices", headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200


# --- /quote (live delivery-fee estimate) ----------------------------------------------------------
class _FakeQuoteCourier:
    """calculate() returns a base shipping fee + a COD surcharge when cod_amount>0 — so the endpoint's
    'two calls, subtract' COD-breakdown is exercised deterministically."""
    def calculate(self, shp):
        base, cod = 6.40, (0.60 if shp.cod_amount > 0 else 0.0)
        return {"price": round(base + cod, 2), "currency": "EUR", "courier": "econt"}


@pytest.fixture
def _fake_quote(monkeypatch):
    monkeypatch.setattr(pc.courier_mod, "get", lambda k: _FakeQuoteCourier())


async def test_quote_office_with_cod_breaks_out_the_surcharge(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get(
        "/api/public/couriers/econt/quote?dest_type=office&office_id=5250&weight_g=5000&cod=true&amount_eur=18.49",
        headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["carrier"] == "econt" and b["currency"] == "EUR" and b["estimated"] is True
    assert b["shipping_fee_eur"] == 6.40
    assert b["cod_fee_eur"] == 0.60
    assert b["total_fee_eur"] == 7.00
    assert b["meta"]["weight_g"] == 5000 and b["meta"]["office_id"] == "5250"


async def test_quote_prepaid_has_no_cod_fee(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get(
        "/api/public/couriers/speedy/quote?dest_type=door&city=Свищов&weight_g=3000&cod=false",
        headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["shipping_fee_eur"] == 6.40 and b["cod_fee_eur"] == 0.0 and b["total_fee_eur"] == 6.40


async def test_quote_office_requires_office_id(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/quote?dest_type=office&weight_g=1000",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 400


async def test_quote_door_requires_city(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/quote?dest_type=door&weight_g=1000",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 400


async def test_quote_needs_token(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/quote?dest_type=office&office_id=5250&weight_g=1000")
    assert r.status_code == 401


async def test_quote_unknown_courier_404(client, _fake_quote, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/nope/quote?dest_type=office&office_id=1&weight_g=1000",
                         headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 404
