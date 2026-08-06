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
    assert prepaid["rates"][0]["price_eur"] == 3.94
    assert cod["rates"][0]["price_eur"] == 4.42
