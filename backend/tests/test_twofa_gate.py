# -*- coding: utf-8 -*-
"""2FA phase 2 — the LOGIN GATE.

The safety claims this file exists to keep honest:
  1. An un-enrolled account logs in exactly as before (204 + cookie) — so switching the gate on
     cannot lock anyone out. This is the single most important test in the file.
  2. An enrolled account gets a ticket, NOT a session cookie.
  3. The ticket alone is worthless: a wrong code is refused, and the ticket is single-use.
  4. A recovery code works EXACTLY once.
  5. Both login paths behave the same (local password and Barsy).

NB `_fresh()` rather than the `client` fixture: conftest's `owner_client` LOGS IN ON `client`
and returns that same object, so reusing `client` in a test that also uses `owner_client` gives
you an already-authenticated session — which would make the "not logged in yet" assertions pass
for the wrong reason (it bit the mail tests too).
"""
import httpx
import pyotp
import pytest
from httpx import ASGITransport

from app.main import app
from app.routers import twofa


def _fresh() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


async def _enroll(client) -> str:
    """Enroll the client's account; returns the TOTP secret."""
    setup = (await client.post("/api/auth/2fa/setup")).json()
    r = await client.post("/api/auth/2fa/verify", json={"code": _code(setup["secret"])})
    assert r.status_code == 200
    return setup["secret"]


async def _password_ticket(c: httpx.AsyncClient) -> str:
    from conftest import _OWNER
    r = await c.post("/api/auth/login",
                     data={"username": _OWNER["email"], "password": _OWNER["password"]},
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


@pytest.mark.asyncio
async def test_unenrolled_login_is_completely_unchanged(client):
    """THE rollout-safety test: no enrollment → the old 204 + cookie, no ticket anywhere."""
    from conftest import _OWNER
    await client.post("/api/auth/setup", json=_OWNER)
    r = await client.post("/api/auth/login",
                          data={"username": _OWNER["email"], "password": _OWNER["password"]},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (200, 204)
    assert "twofa_required" not in (r.text or "")
    assert (await client.get("/api/users/me")).status_code == 200      # session really works


@pytest.mark.asyncio
async def test_enrolled_login_returns_ticket_not_cookie(owner_client):
    from conftest import _OWNER
    secret = await _enroll(owner_client)
    async with _fresh() as fresh:
        r = await fresh.post("/api/auth/login",
                             data={"username": _OWNER["email"], "password": _OWNER["password"]},
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = r.json()
        assert r.status_code == 200 and body["twofa_required"] is True and body["ticket"]
        assert "darling_admin" not in r.cookies                 # NOT logged in yet
        assert (await fresh.get("/api/users/me")).status_code == 401

        ok = await fresh.post("/api/auth/2fa/login",
                              json={"ticket": body["ticket"], "code": _code(secret)})
        assert ok.status_code in (200, 204)
        assert (await fresh.get("/api/users/me")).status_code == 200   # now logged in


@pytest.mark.asyncio
async def test_wrong_code_refused_and_ticket_is_single_use(owner_client):
    secret = await _enroll(owner_client)
    async with _fresh() as fresh:
        t = await _password_ticket(fresh)
        assert (await fresh.post("/api/auth/2fa/login",
                                 json={"ticket": t, "code": "000000"})).status_code == 401
        assert (await fresh.get("/api/users/me")).status_code == 401    # still out

        assert (await fresh.post("/api/auth/2fa/login",
                                 json={"ticket": t, "code": _code(secret)})).status_code in (200, 204)
        # replaying the SAME ticket must fail even with a valid code
        assert (await fresh.post("/api/auth/2fa/login",
                                 json={"ticket": t, "code": _code(secret)})).status_code == 401


@pytest.mark.asyncio
async def test_recovery_code_works_exactly_once(owner_client):
    await _enroll(owner_client)
    codes = (await owner_client.post("/api/auth/2fa/recovery/regenerate")).json()["recovery_codes"]
    async with _fresh() as fresh:
        first = await fresh.post("/api/auth/2fa/login",
                                 json={"ticket": await _password_ticket(fresh),
                                       "recovery_code": codes[0]})
        assert first.status_code in (200, 204)
        # the same recovery code is now burned
        second = await fresh.post("/api/auth/2fa/login",
                                  json={"ticket": await _password_ticket(fresh),
                                        "recovery_code": codes[0]})
        assert second.status_code == 401
        # a different one still works
        third = await fresh.post("/api/auth/2fa/login",
                                 json={"ticket": await _password_ticket(fresh),
                                       "recovery_code": codes[1]})
        assert third.status_code in (200, 204)
    assert (await owner_client.get("/api/auth/2fa/status")).json()["recovery_remaining"] == \
        twofa.N_RECOVERY - 2


@pytest.mark.asyncio
async def test_garbage_and_wrong_audience_tickets_are_rejected(client):
    assert (await client.post("/api/auth/2fa/login",
                              json={"ticket": "definitely-not-a-jwt-token",
                                    "code": "123456"})).status_code == 401
    # a syntactically valid token minted for a DIFFERENT purpose must not open the door
    from fastapi_users.jwt import generate_jwt

    from app.config import get_settings
    wrong = generate_jwt({"sub": "x", "aud": "some:other"}, get_settings().jwt_secret,
                         lifetime_seconds=300)
    assert (await client.post("/api/auth/2fa/login",
                              json={"ticket": wrong, "code": "123456"})).status_code == 401


@pytest.mark.asyncio
async def test_barsy_login_honours_the_same_gate(manager_client, monkeypatch):
    """The Barsy path writes the cookie by hand, so it needs its own proof."""
    secret = await _enroll(manager_client)
    import app.routers.auth_extra as ax
    monkeypatch.setattr(ax.barsy_auth, "verify", lambda u, p: {
        "user_id": 100, "user_name": "mgr", "role_id": 1, "role_name": "Мениджър",
        "fname": "Мария", "lname": "Мениджър", "email": "mgr@x.bg"})
    async with _fresh() as fresh:
        r = await fresh.post("/api/auth/barsy-login", json={"username": "mgr", "password": "x"})
        body = r.json()
        assert r.status_code == 200 and body.get("twofa_required") is True and body.get("ticket")
        assert (await fresh.get("/api/users/me")).status_code == 401
        ok = await fresh.post("/api/auth/2fa/login",
                              json={"ticket": body["ticket"], "code": _code(secret)})
        assert ok.status_code in (200, 204)
        assert (await fresh.get("/api/users/me")).status_code == 200


@pytest.mark.asyncio
async def test_recovery_codes_are_issued_once_and_counted(owner_client):
    await _enroll(owner_client)
    codes = (await owner_client.post("/api/auth/2fa/recovery/regenerate")).json()["recovery_codes"]
    st = (await owner_client.get("/api/auth/2fa/status")).json()
    assert st["recovery_remaining"] == len(codes) == twofa.N_RECOVERY
    assert all("$argon2" not in c for c in codes)      # user sees plaintext; store keeps hashes
    assert "recovery_codes" not in st                  # status never re-issues them
