# -*- coding: utf-8 -*-
"""Delivery fees (DELIVERY_FEE_DESIGN v2): refresh from fake adapters, owner fields untouched,
public checkout endpoint math (buffer + COD-in-price), bracket pick, cheapest-first, token gate,
failed-refresh keeps old rows."""
from types import SimpleNamespace

import pytest

from app.routers import shipping_rates as sr

TOKEN = "test-office-token-123"


class FakeAdapter:
    """Distance-flat fake: office 3.64/6.35..., door +2.55; COD adds 0.48 to the quote."""

    def __init__(self, name, fail=False):
        self.name, self.fail = name, fail

    def configured(self):
        return True

    def offices(self, city=None):
        return [{"id": 90, "name": "x", "address": "", "city": ""}]

    def calculate(self, shp):
        if self.fail:
            raise RuntimeError("quote API down")
        base = {1.0: 3.64, 3.0: 3.64, 5.0: 6.35, 10.0: 7.2}[float(shp.weight_kg)]
        if shp.office_id is None:
            base += 2.55
        if shp.cod_amount:
            base += 0.48
        return {"price": round(base, 2), "currency": "EUR", "courier": self.name}


class FakeBoxNow(FakeAdapter):
    """Contract-flat like the real adapter: 1.54 EUR any weight/destination, COD delta 0."""

    def calculate(self, shp):
        if self.fail:
            raise RuntimeError("quote API down")
        return {"price": 1.54, "currency": "EUR", "courier": "boxnow"}


@pytest.fixture()
def fake_couriers(monkeypatch):
    monkeypatch.setattr(sr.courier_mod, "get",
                        lambda n: FakeBoxNow(n) if n == "boxnow" else FakeAdapter(n))
    return None


@pytest.fixture()
def public_token(monkeypatch):
    monkeypatch.setattr(sr, "get_settings", lambda: SimpleNamespace(office_lookup_token=TOKEN))
    return TOKEN


@pytest.mark.asyncio
async def test_refresh_builds_rows_with_owner_defaults(owner_client, fake_couriers):
    r = (await owner_client.post("/api/shipping-rates/refresh")).json()
    assert r["ok"] is True and r["couriers"]["speedy"].startswith("ok")
    rows = (await owner_client.get("/api/shipping-rates")).json()["rates"]
    sp_office_1 = next(x for x in rows if x["courier"] == "speedy" and x["mode"] == "office" and x["max_kg"] == 1.0)
    assert sp_office_1["label_cost"] == 3.64
    assert sp_office_1["cod_fee"] == 0.48                     # quote(COD 50) − quote(0)
    assert sp_office_1["buffer"] == 0.30                      # owner-approved default (Speedy office)
    assert sp_office_1["enabled"] is True
    pigeon = [x for x in rows if x["courier"] == "pigeon"]
    assert pigeon and all(not x["enabled"] for x in pigeon)   # priced but DARK (no Svishtov office)


@pytest.mark.asyncio
async def test_refresh_failure_keeps_old_rows(owner_client, fake_couriers, monkeypatch):
    await owner_client.post("/api/shipping-rates/refresh")
    before = (await owner_client.get("/api/shipping-rates")).json()["rates"]
    monkeypatch.setattr(sr.courier_mod, "get", lambda n: FakeAdapter(n, fail=True))
    r = (await owner_client.post("/api/shipping-rates/refresh")).json()
    assert all(v.startswith("error") for v in r["couriers"].values())
    after = (await owner_client.get("/api/shipping-rates")).json()["rates"]
    assert [x["label_cost"] for x in after] == [x["label_cost"] for x in before]   # prices survived


@pytest.mark.asyncio
async def test_public_rates_math_and_order(owner_client, client, fake_couriers, public_token):
    await owner_client.post("/api/shipping-rates/refresh")
    r = (await client.get("/api/public/shipping-rates?weight_kg=1&cod=0",
                          headers={"X-Office-Token": TOKEN})).json()
    assert r["free_over_eur"] == 77.0
    # Cheapest first is still the rule; what is cheapest changed. Since the BOX NOW test order
    # was confirmed (2026-08-07) a boxnow locker row is seeded enabled at 1.54 + 0.10 = 1.64,
    # which undercuts every counter tariff - so it leads the list and speedy is read by NAME.
    first = r["rates"][0]
    assert first["courier"] == "boxnow" and first["mode"] == "locker"
    assert first["price_eur"] == 1.64
    assert first["sort"] == 1
    sp = next(x for x in r["rates"] if x["courier"] == "speedy" and x["mode"] == "office")
    assert sp["price_eur"] == 3.94                                         # 3.64 + 0.30 buffer
    cod_body = (await client.get("/api/public/shipping-rates?weight_kg=1&cod=1",
                                 headers={"X-Office-Token": TOKEN})).json()
    sp_cod = next(x for x in cod_body["rates"] if x["courier"] == "speedy" and x["mode"] == "office")
    assert sp_cod["price_eur"] == 4.42                                     # + 0.48 COD, no label
    bx_cod = next(x for x in cod_body["rates"] if x["courier"] == "boxnow")
    assert bx_cod["price_eur"] == 1.64                    # COD fee 0 - card-by-link, never cash

@pytest.mark.asyncio
async def test_public_rates_bracket_pick(owner_client, client, fake_couriers, public_token):
    await owner_client.post("/api/shipping-rates/refresh")
    r = (await client.get("/api/public/shipping-rates?weight_kg=4&cod=0",
                          headers={"X-Office-Token": TOKEN})).json()
    sp = next(x for x in r["rates"] if x["courier"] == "speedy" and x["mode"] == "office")
    assert sp["price_eur"] == 6.65                             # 5kg bracket (6.35) + 0.30
    heavy = (await client.get("/api/public/shipping-rates?weight_kg=25&cod=0",
                              headers={"X-Office-Token": TOKEN})).json()
    sp = next(x for x in heavy["rates"] if x["courier"] == "speedy" and x["mode"] == "office")
    assert sp["price_eur"] == 7.5                              # falls back to the 10kg bracket


@pytest.mark.asyncio
async def test_public_rates_token_gate(client, public_token):
    assert (await client.get("/api/public/shipping-rates")).status_code == 401
    # no token configured => endpoint disabled entirely
    from types import SimpleNamespace as NS

    import app.routers.shipping_rates as srm
    old = srm.get_settings
    srm.get_settings = lambda: NS(office_lookup_token="")
    try:
        assert (await client.get("/api/public/shipping-rates",
                                 headers={"X-Office-Token": "x"})).status_code == 403
    finally:
        srm.get_settings = old


@pytest.mark.asyncio
async def test_rate_patch_is_manager_gated_and_bounded(owner_client, staff_client, fake_couriers):
    await owner_client.post("/api/shipping-rates/refresh")
    rows = (await owner_client.get("/api/shipping-rates")).json()["rates"]
    rid = rows[0]["id"]
    assert (await staff_client.patch(f"/api/shipping-rates/{rid}", json={"buffer": 0.4})).status_code == 403
    assert (await owner_client.patch(f"/api/shipping-rates/{rid}", json={"buffer": 9.99})).status_code == 400
    ok = (await owner_client.patch(f"/api/shipping-rates/{rid}", json={"buffer": 0.4, "enabled": False})).json()
    assert ok["ok"] is True
    row = next(x for x in (await owner_client.get("/api/shipping-rates")).json()["rates"] if x["id"] == rid)
    assert row["buffer"] == 0.4 and row["enabled"] is False


@pytest.mark.asyncio
async def test_boxnow_locker_rows_flat_dark_and_labeled(owner_client, fake_couriers, client, public_token):
    """BoxNow: locker-only rows at the contract-flat price, cod_fee 0 (card-by-link COD),
    owner-default buffer 0.10.

    Was DARK by default until 2026-08-07, when the owner confirmed the BOX NOW test order. The
    spec flag now seeds new brackets ENABLED, so a fresh refresh serves it - and at 1.64 it is
    the cheapest thing on the list. Rows that already existed keep whatever the owner set; that
    divergence is reported as spec_enabled, it is not reconciled behind his back."""
    await owner_client.post("/api/shipping-rates/refresh")
    rows = (await owner_client.get("/api/shipping-rates")).json()["rates"]
    bx = [x for x in rows if x["courier"] == "boxnow"]
    assert bx and all(x["mode"] == "locker" for x in bx)
    assert all(x["label_cost"] == 1.54 and x["cod_fee"] == 0.0 and x["buffer"] == 0.10 for x in bx)
    assert all(x["enabled"] is True for x in bx)          # seeded ON since the test order
    assert all(x["spec_enabled"] is True for x in bx)     # ...and the contract agrees
    pub = (await client.get("/api/public/shipping-rates?weight_kg=2&cod=1",
                            headers={"X-Office-Token": TOKEN})).json()
    assert any(r["courier"] == "boxnow" for r in pub["rates"])
    # And the owner can still take it back off: the switch is his, not the spec's. It takes ALL
    # FOUR brackets - turning off the 1 kg row alone just hands a 0.5 kg parcel to the 3 kg row,
    # which is the bracket fallback working, not the carrier going dark.
    rid = bx[0]["id"]
    await owner_client.patch(f"/api/shipping-rates/{rid}", json={"enabled": False})
    still = (await client.get("/api/public/shipping-rates?weight_kg=0.5&cod=1",
                              headers={"X-Office-Token": TOKEN})).json()
    bx_still = next(r for r in still["rates"] if r["courier"] == "boxnow")
    assert bx_still["price_eur"] == 1.64            # same flat price from the next bracket up
    for row in bx:
        await owner_client.patch(f"/api/shipping-rates/{row['id']}", json={"enabled": False})
    off = (await client.get("/api/public/shipping-rates?weight_kg=0.5&cod=1",
                            headers={"X-Office-Token": TOKEN})).json()
    assert all(r["courier"] != "boxnow" for r in off["rates"])
    for row in bx:
        await owner_client.patch(f"/api/shipping-rates/{row['id']}", json={"enabled": True})
    pub2 = (await client.get("/api/public/shipping-rates?weight_kg=0.5&cod=1",
                             headers={"X-Office-Token": TOKEN})).json()
    got = next(r for r in pub2["rates"] if r["courier"] == "boxnow")
    assert got["label_bg"] == "BoxNow — до автомат"
    assert got["price_eur"] == 1.64                                     # 1.54 + 0.10, no COD fee
    assert got["sort"] == 1                                             # cheapest first
