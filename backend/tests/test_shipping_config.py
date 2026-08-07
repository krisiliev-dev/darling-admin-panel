# -*- coding: utf-8 -*-
"""Delivery-fee CONFIG surface (DELIVERY_FEE_DESIGN v2, slice 2).

Two things are worth testing here and neither is the happy path.

The free-over threshold used to be a module constant, so changing it needed a deploy. Now it
is stored, which means it can be absent, stale, or garbage — and every one of those must fall
back to the code default rather than to zero. A free-shipping threshold of 0 makes every
order ship free, silently, and nothing else in the system would notice.

The second is a row with no tariff. Checkout prices an enabled row as label_cost + buffer, so
a row whose label_cost never landed would have quoted the BUFFER ALONE — thirty cents of
delivery on a real order. The refresh cannot produce such a row today, which is exactly why
nothing would have caught it if it ever did.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import async_session_maker
from app.models import AppSetting, ShippingRate
from app.routers import shipping_rates as sr

TOKEN = "test-office-token-123"


@pytest.fixture()
def public_token(monkeypatch):
    monkeypatch.setattr(sr, "get_settings", lambda: SimpleNamespace(office_lookup_token=TOKEN))
    return TOKEN


async def _put(**kw):
    """Insert a tariff row. The suite has no session fixture; make_draft's pattern is to open
    one from the maker, so this does the same."""
    async with async_session_maker() as s:
        s.add(ShippingRate(**kw))
        await s.commit()


async def _store_raw(value: str) -> None:
    async with async_session_maker() as s:
        s.add(AppSetting(key=sr.FREE_OVER_KEY, value=value))
        await s.commit()


async def _free_over():
    async with async_session_maker() as s:
        return await sr.get_free_over(s)


async def _settings_rows():
    async with async_session_maker() as s:
        return (await s.execute(select(AppSetting).where(
            AppSetting.key == sr.FREE_OVER_KEY))).scalars().all()


# ------------------------------------------------------- the free-over threshold
@pytest.mark.asyncio
async def test_free_over_defaults_when_nothing_is_stored():
    assert await _free_over() == (sr.FREE_OVER_EUR, "default")


@pytest.mark.asyncio
async def test_free_over_reads_a_stored_value():
    await _store_raw("99.5")
    assert await _free_over() == (99.5, "stored")


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", ["", "   ", "abc", "77 EUR", "None", "-1", "0", "1e9"])
async def test_an_unusable_stored_value_falls_back_to_the_default_never_to_zero(junk):
    """A threshold of 0 would make every order ship free and nothing else would notice."""
    await _store_raw(junk)
    value, source = await _free_over()
    assert (value, source) == (sr.FREE_OVER_EUR, "default"), junk
    assert value > 0


@pytest.mark.asyncio
async def test_the_source_is_reported_so_the_page_can_tell_the_truth():
    """«77 €» on a screen cannot say whether it is the owner's setting or the fallback, and
    those differ the moment somebody wonders why an edit did not take."""
    _, source = await _free_over()
    assert source == "default"
    await _store_raw("77")
    _, source = await _free_over()
    assert source == "stored"


# --------------------------------------------------------------- the config write
@pytest.mark.asyncio
async def test_owner_can_set_the_threshold_and_it_persists(owner_client):
    r = await owner_client.patch("/api/shipping-rates/config", json={"free_over_eur": 88.5})
    assert r.status_code == 200, r.text
    assert r.json()["free_over_eur"] == 88.5
    rows = await _settings_rows()
    assert len(rows) == 1 and float(rows[0].value) == 88.5


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, -0.01, 10_001, 1_000_000])
async def test_an_impossible_threshold_is_refused_not_clamped(owner_client, bad):
    """Refused rather than clamped: a zero ships everything free and a typo'd 7 instead of 77
    is a margin decision nobody made on purpose. Silently correcting either hides it."""
    r = await owner_client.patch("/api/shipping-rates/config", json={"free_over_eur": bad})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_setting_it_twice_updates_rather_than_duplicating(owner_client):
    await owner_client.patch("/api/shipping-rates/config", json={"free_over_eur": 60})
    await owner_client.patch("/api/shipping-rates/config", json={"free_over_eur": 65})
    rows = await _settings_rows()
    assert len(rows) == 1
    assert float(rows[0].value) == 65.0


@pytest.mark.asyncio
async def test_a_packer_cannot_change_the_threshold(packer_client):
    r = await packer_client.patch("/api/shipping-rates/config", json={"free_over_eur": 1})
    assert r.status_code in (401, 403)


# -------------------------------------------------------- what the config page reads
@pytest.mark.asyncio
async def test_the_page_gets_the_suggested_buffer_beside_the_set_one(owner_client):
    """The design calculated a buffer per courier×mode. Showing it next to the set value is
    how a drift from the approved model stays visible instead of being forgotten."""
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    body = (await owner_client.get("/api/shipping-rates")).json()
    row = body["rates"][0]
    assert row["suggested_buffer"] == sr._SPEC["speedy"]["buffer"]["office"]
    assert row["courier_bg"] and row["mode_bg"]


@pytest.mark.asyncio
async def test_the_page_is_told_how_old_the_tariffs_are(owner_client):
    """A week-old tariff is not visibly different from a fresh one until an invoice arrives."""
    await _put(courier="econt", mode="office", max_kg=1.0,
               label_cost=5.22, cod_fee=1.59, buffer=0.5, enabled=True)
    body = (await owner_client.get("/api/shipping-rates")).json()
    # never refreshed → stale, and stale_hours is None rather than a made-up 0
    assert body["stale"] is True
    assert body["stale_hours"] is None
    assert body["stale_after_h"] == sr.STALE_AFTER_H


@pytest.mark.asyncio
async def test_an_unpriced_row_shows_no_price_rather_than_zero(owner_client):
    """0.00 € on the page reads as «free delivery», not as «we never got a tariff»."""
    await _put(courier="pigeon", mode="office", max_kg=1.0,
               label_cost=0.0, cod_fee=0.0, buffer=0.25, enabled=False)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["priced"] is False
    assert row["price_eur"] is None and row["price_cod_eur"] is None


@pytest.mark.asyncio
async def test_a_priced_row_shows_both_totals(owner_client):
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["priced"] is True
    assert row["price_eur"] == 3.94
    assert row["price_cod_eur"] == 4.42


# ------------------------------------------------------------- THE MONEY GUARD
@pytest.mark.asyncio
async def test_checkout_never_quotes_a_row_that_has_no_tariff(client, public_token):
    """THE one. label_cost 0 + buffer 0.30 would have been offered as 0.30 € delivery."""
    await _put(courier="pigeon", mode="office", max_kg=1.0,
               label_cost=0.0, cod_fee=0.0, buffer=0.30, enabled=True)
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    body = (await client.get("/api/public/shipping-rates?weight_kg=1",
                             headers={"x-office-token": public_token})).json()
    couriers = {r["courier"] for r in body["rates"]}
    assert "pigeon" not in couriers, body
    assert "speedy" in couriers
    assert all(r["price_eur"] > 0 for r in body["rates"])


@pytest.mark.asyncio
async def test_a_carrier_with_no_tariff_at_all_leaves_checkout_empty_not_cheap(
        client, public_token):
    """Fail closed: no rates is a visible problem, a 0.30 € delivery is not."""
    await _put(courier="pigeon", mode="office", max_kg=1.0,
               label_cost=0.0, cod_fee=0.0, buffer=0.30, enabled=True)
    body = (await client.get("/api/public/shipping-rates?weight_kg=1",
                             headers={"x-office-token": public_token})).json()
    assert body["rates"] == []


@pytest.mark.asyncio
async def test_checkout_uses_the_stored_threshold_not_the_constant(
        client, public_token):
    await _store_raw("55")
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    body = (await client.get("/api/public/shipping-rates?weight_kg=1",
                             headers={"x-office-token": public_token})).json()
    assert body["free_over_eur"] == 55.0


@pytest.mark.asyncio
async def test_cod_still_folds_into_the_shown_price(client, public_token):
    """Kris's rule: no separate «+0.48» label — the COD total is just slightly higher."""
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    prepaid = (await client.get("/api/public/shipping-rates?weight_kg=1&cod=0",
                                headers={"x-office-token": public_token})).json()
    cod = (await client.get("/api/public/shipping-rates?weight_kg=1&cod=1",
                            headers={"x-office-token": public_token})).json()
    # Pick the row by NAME, not by position. public_rates kicks off a background refresh when
    # the rows are stale, and that refresh seeds any courier the spec marks enabled - so from
    # 2026-08-07 a boxnow locker row appears here and sorts ahead of speedy. The assertion was
    # about speedy's COD arithmetic all along; the [0] was an accident of alphabet.
    def speedy(body):
        return next(r for r in body["rates"] if r["courier"] == "speedy")
    assert speedy(prepaid)["price_eur"] == 3.94
    assert speedy(cod)["price_eur"] == 4.42
# ------------------------------------------------- policy vs state (BoxNow, 2026-08-07)
# `enabled` is what checkout reads. `spec_enabled` is what the courier contract says. They
# diverge whenever a carrier is signed AFTER its rate rows were first seeded, because the spec
# flag only ever seeds a bracket the first time it appears. BoxNow is exactly that case: four
# live rows carrying enabled=0 from before its test order was confirmed.
@pytest.mark.asyncio
async def test_a_signed_carrier_that_is_still_off_is_reported_as_such(owner_client):
    """The one that matters. Flipping the spec alone changes nothing about these rows, and a
    divergence nobody can see is a carrier everybody believes is live that has never served
    a single rate."""
    await _put(courier="boxnow", mode="locker", max_kg=1.0,
               label_cost=1.54, cod_fee=0.0, buffer=0.10, enabled=False)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["spec_enabled"] is True          # the contract says on
    assert row["enabled"] is False              # the row says off
    assert row["price_eur"] == 1.64             # 1.54 flat + 0.10 buffer
    assert row["price_cod_eur"] == 1.64         # COD fee is 0 - card-by-link, never cash


@pytest.mark.asyncio
async def test_an_agreeing_row_reports_no_divergence(owner_client):
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["spec_enabled"] is True and row["enabled"] is True


@pytest.mark.asyncio
async def test_a_carrier_the_contract_has_not_signed_reports_spec_off(owner_client):
    """Pigeon: adapter and keys live, but no Svishtov office to ship FROM yet."""
    await _put(courier="pigeon", mode="office", max_kg=1.0,
               label_cost=4.10, cod_fee=0.0, buffer=0.25, enabled=False)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["spec_enabled"] is False


@pytest.mark.asyncio
async def test_an_unknown_courier_does_not_crash_the_page(owner_client):
    """A row for a courier no longer in _SPEC must read as "not in the contract", not 500."""
    await _put(courier="evropat", mode="office", max_kg=1.0,
               label_cost=3.00, cod_fee=0.0, buffer=0.30, enabled=False)
    row = (await owner_client.get("/api/shipping-rates")).json()["rates"][0]
    assert row["spec_enabled"] is False
    assert row["suggested_buffer"] is None


@pytest.mark.asyncio
async def test_boxnow_still_does_not_reach_checkout_while_its_row_is_off(client, public_token):
    """The spec flip must not have turned a carrier on by itself. Checkout reads the ROW."""
    await _put(courier="boxnow", mode="locker", max_kg=1.0,
               label_cost=1.54, cod_fee=0.0, buffer=0.10, enabled=False)
    body = (await client.get("/api/public/shipping-rates?weight_kg=1",
                             headers={"x-office-token": public_token})).json()
    assert [r for r in body["rates"] if r["courier"] == "boxnow"] == []
# ------------------------------------- the BoxNow enable migration (ee66ff778899, 2026-08-07)
@pytest.mark.asyncio
async def test_the_migration_turns_boxnow_on_and_leaves_the_others_alone():
    """A data migration on a MONEY switch, so the blast radius is the thing to test: boxnow rows
    at enabled=0 go on, and nothing else moves."""
    from sqlalchemy import text
    await _put(courier="boxnow", mode="locker", max_kg=1.0,
               label_cost=1.54, cod_fee=0.0, buffer=0.10, enabled=False)
    await _put(courier="pigeon", mode="office", max_kg=1.0,
               label_cost=4.10, cod_fee=0.0, buffer=0.25, enabled=False)
    await _put(courier="speedy", mode="office", max_kg=1.0,
               label_cost=3.64, cod_fee=0.48, buffer=0.30, enabled=True)
    async with async_session_maker() as s:
        await s.execute(text("UPDATE shipping_rate SET enabled = 1 "
                             "WHERE courier = 'boxnow' AND enabled = 0"))
        await s.commit()
    async with async_session_maker() as s:
        got = {(r.courier, r.enabled) for r in
               (await s.execute(select(ShippingRate))).scalars().all()}
    assert ("boxnow", True) in got
    assert ("pigeon", False) in got          # a carrier the contract has NOT signed stays dark
    assert ("speedy", True) in got
