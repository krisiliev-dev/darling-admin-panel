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


async def test_unknown_courier_404(client, _fake_courier, monkeypatch):
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/nope/offices", headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 404


async def test_token_holder_needs_no_panel_login(client, _fake_courier, monkeypatch):
    # the whole point: `client` is UNAUTHENTICATED (no panel cookie) yet succeeds with the token
    _set_token(monkeypatch, "s3cret-abc")
    r = await client.get("/api/public/couriers/econt/offices", headers={"x-office-token": "s3cret-abc"})
    assert r.status_code == 200
