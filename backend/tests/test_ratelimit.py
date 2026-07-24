# -*- coding: utf-8 -*-
"""Auth rate-limiting: per-(ip,user) failed-login lockout + per-ip cap. Brute-force defence."""
import pytest
import pytest_asyncio

from app import ratelimit
from app.routers import auth_extra

# the limiter's global state is cleared around every test by the autouse _reset_rate_limiter
# fixture in conftest.py, so these tests each start from a clean window.


@pytest_asyncio.fixture
def set_verify(monkeypatch):
    def _apply(value):
        def _fake(_u, _p):
            if isinstance(value, Exception):
                raise value
            return value
        monkeypatch.setattr(auth_extra.barsy_auth, "verify", _fake)
    return _apply


async def _login(client, username="attacker", password="wrong"):
    return await client.post("/api/auth/barsy-login", json={"username": username, "password": password})


async def test_lockout_after_five_failures(client, set_verify):
    set_verify(None)  # every attempt is a wrong password
    for _ in range(ratelimit.FAIL_LIMIT):
        assert (await _login(client)).status_code == 401
    r = await _login(client)
    assert r.status_code == 429
    assert any(k.lower() == "retry-after" for k in r.headers)


async def test_lockout_is_per_username(client, set_verify):
    set_verify(None)
    for _ in range(ratelimit.FAIL_LIMIT):
        await _login(client, username="victim1")
    assert (await _login(client, username="victim1")).status_code == 429
    # a different username from the same client is NOT locked
    assert (await _login(client, username="victim2")).status_code == 401


async def test_success_resets_failure_counter(client, set_verify, monkeypatch):
    set_verify(None)
    for _ in range(ratelimit.FAIL_LIMIT - 1):  # 4 fails — one short of lockout
        await _login(client, username="claude")
    # a correct login clears the counter…
    set_verify({"user_id": 1, "role_name": "Мениджър", "user_name": "claude", "email": "c@x.com"})
    assert (await _login(client, username="claude", password="right")).status_code == 200
    # …so the next wrong password starts fresh (401, not 429)
    set_verify(None)
    assert (await _login(client, username="claude")).status_code == 401


async def test_per_ip_request_cap(client, set_verify):
    set_verify({"user_id": 1, "role_name": "Мениджър", "user_name": "u", "email": "u@x.com"})
    # each username differs so the per-user lockout never trips — only the per-ip cap should
    seen_429 = False
    for i in range(ratelimit.REQ_LIMIT + 2):
        r = await _login(client, username=f"user{i}", password="right")
        if r.status_code == 429:
            seen_429 = True
            break
    assert seen_429, "per-ip request cap should eventually 429"
