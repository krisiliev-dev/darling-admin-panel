# -*- coding: utf-8 -*-
"""Newsletter subscribe proxy: token gate, validation, rate limit, no-enumeration duplicates,
honest 503 on listmonk failure. The listmonk HTTP call is stubbed at the module seam
(_listmonk_subscribe) — CI has no listmonk; the live RESULT gate runs on the box."""
from types import SimpleNamespace

import pytest

from app.routers import newsletter as nl

TOKEN = "test-office-token-123"


@pytest.fixture()
def wired(monkeypatch):
    monkeypatch.setattr(nl, "get_settings", lambda: SimpleNamespace(office_lookup_token=TOKEN))
    monkeypatch.setattr(nl, "_hits", {})
    calls: list = []

    def fake_sub(email, source):
        calls.append((email, source))
        return "pending"

    monkeypatch.setattr(nl, "_listmonk_subscribe", fake_sub)
    return calls


@pytest.mark.asyncio
async def test_token_gate(client, wired, monkeypatch):
    body = {"email": "a@b.bg"}
    assert (await client.post("/api/public/newsletter/subscribe", json=body)).status_code == 401
    monkeypatch.setattr(nl, "get_settings", lambda: SimpleNamespace(office_lookup_token=""))
    assert (await client.post("/api/public/newsletter/subscribe", json=body,
                              headers={"X-Office-Token": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_subscribe_happy_path_normalizes_and_forwards(client, wired):
    r = await client.post("/api/public/newsletter/subscribe",
                          json={"email": "  KLIENT@Primer.BG ", "source": "footer"},
                          headers={"X-Office-Token": TOKEN})
    assert r.status_code == 200 and r.json() == {"status": "pending"}
    assert wired == [("klient@primer.bg", "footer")]


@pytest.mark.asyncio
async def test_validation_and_consent(client, wired):
    h = {"X-Office-Token": TOKEN}
    assert (await client.post("/api/public/newsletter/subscribe",
                              json={"email": "not-an-email"}, headers=h)).status_code == 400
    assert (await client.post("/api/public/newsletter/subscribe",
                              json={"email": "a@b.bg", "consent": False}, headers=h)).status_code == 400
    assert wired == []


@pytest.mark.asyncio
async def test_duplicate_is_indistinguishable(client, wired, monkeypatch):
    """No enumeration: listmonk-duplicate answers exactly like a fresh subscribe."""
    monkeypatch.setattr(nl, "_listmonk_subscribe", lambda e, s: "pending")  # dup path returns pending too
    h = {"X-Office-Token": TOKEN}
    r1 = await client.post("/api/public/newsletter/subscribe", json={"email": "x@y.bg"}, headers=h)
    r2 = await client.post("/api/public/newsletter/subscribe", json={"email": "x@y.bg"}, headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == {"status": "pending"}


@pytest.mark.asyncio
async def test_listmonk_down_is_honest_503(client, wired, monkeypatch):
    def boom(email, source):
        raise RuntimeError("listmonk HTTP 500: down")

    monkeypatch.setattr(nl, "_listmonk_subscribe", boom)
    r = await client.post("/api/public/newsletter/subscribe", json={"email": "a@b.bg"},
                          headers={"X-Office-Token": TOKEN})
    assert r.status_code == 503                      # never a fake "pending"


@pytest.mark.asyncio
async def test_rate_limit_trips_per_ip(client, wired, monkeypatch):
    monkeypatch.setattr(nl, "_PER_IP_PER_HOUR", 3)
    h = {"X-Office-Token": TOKEN, "X-Forwarded-For": "10.7.7.7"}
    for i in range(3):
        assert (await client.post("/api/public/newsletter/subscribe",
                                  json={"email": f"u{i}@d.bg"}, headers=h)).status_code == 200
    assert (await client.post("/api/public/newsletter/subscribe",
                              json={"email": "u9@d.bg"}, headers=h)).status_code == 429
    h2 = {"X-Office-Token": TOKEN, "X-Forwarded-For": "10.7.7.8"}
    assert (await client.post("/api/public/newsletter/subscribe",
                              json={"email": "z@d.bg"}, headers=h2)).status_code == 200
