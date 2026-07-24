# -*- coding: utf-8 -*-
"""First-run owner setup guard — the security-critical invariant.

The /setup endpoint mints the very first owner. It must be usable exactly ONCE: the moment
any user exists it must refuse, so it can never be replayed from the open internet to mint a
second admin.
"""


async def test_setup_status_true_when_no_users(client):
    r = await client.get("/api/auth/setup-status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}


async def test_first_setup_creates_owner_and_flips_status(client):
    # needs_setup starts true
    assert (await client.get("/api/auth/setup-status")).json()["needs_setup"] is True

    r = await client.post("/api/auth/setup", json={
        "email": "boss@example.com", "password": "a-strong-pw-123", "full_name": "Boss"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "owner"
    assert body["is_superuser"] is True  # owner must be superuser (gates admin routes)

    # status flips true -> false
    assert (await client.get("/api/auth/setup-status")).json()["needs_setup"] is False


async def test_second_setup_is_refused_with_409(client):
    first = await client.post("/api/auth/setup", json={
        "email": "boss@example.com", "password": "a-strong-pw-123"})
    assert first.status_code == 201

    # a SECOND setup attempt (even with a different email) must be rejected
    second = await client.post("/api/auth/setup", json={
        "email": "intruder@evil.com", "password": "another-pw-123"})
    assert second.status_code == 409

    # and the panel still reports it is set up
    assert (await client.get("/api/auth/setup-status")).json()["needs_setup"] is False
