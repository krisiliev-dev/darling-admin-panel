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


@pytest.fixture()
def fake_couriers(monkeypatch):
    monkeypatch.setattr(sr.courier_mod, "get", lambda n: FakeAdapter(n))
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
    first = r["rates"][0]
    assert first["courier"] == "speedy" and first["mode"] == "office"     # cheapest first
    assert first["price_eur"] == 3.94                                      # 3.64 + 0.30 buffer
    assert first["sort"] == 1
    cod = (await client.get("/api/public/shipping-rates?weight_kg=1&cod=1",
                            headers={"X-Office-Token": TOKEN})).json()["rates"][0]
    assert cod["price_eur"] == 4.42                                        # + 0.48 COD, no label

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
