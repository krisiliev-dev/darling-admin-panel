# -*- coding: utf-8 -*-
"""/api/auth/barsy-login — log in with Barsy credentials.

Barsy is the source of truth for accounts + roles; the panel verifies the credentials against
Barsy (Users_getcurrent), mirrors the user locally with their mapped role, and issues the panel
JWT cookie. We mock barsy_auth.verify so no real ERP call happens — verify's own transport is
covered by its contract (None = bad creds, RuntimeError = Barsy down).
"""
import pytest_asyncio

import app.routers.auth_extra as auth_extra

_PROFILE = {
    "user_id": 42, "role_name": "Мениджър", "email": "Boss@Barsy.Example",
    "fname": "Бойко", "lname": "Мениджъров", "user_name": "boss",
}


@pytest_asyncio.fixture
def mock_verify(monkeypatch):
    """Patch the verify() that barsy-login calls. Returns a setter so each test dictates the
    outcome: a profile dict (valid), None (bad creds), or an exception instance (Barsy down)."""
    state = {"result": _PROFILE}

    def _fake_verify(username, password):
        r = state["result"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(auth_extra.barsy_auth, "verify", _fake_verify)
    return lambda result: state.__setitem__("result", result)


async def test_barsy_login_ok_sets_cookie_and_maps_role(client, mock_verify):
    mock_verify(_PROFILE)
    r = await client.post("/api/auth/barsy-login",
                          json={"username": "boss", "password": "whatever"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Мениджър → manager + superuser (full access)
    assert body["role"] == "manager"
    assert body["is_superuser"] is True
    # email is normalized (lower-cased) from the Barsy profile
    assert body["email"] == "boss@barsy.example"
    # the panel session cookie is set on the client
    assert "darling_admin" in client.cookies


async def test_barsy_login_upserts_local_user_usable_with_cookie(client, mock_verify):
    mock_verify(_PROFILE)
    login = await client.post("/api/auth/barsy-login",
                              json={"username": "boss", "password": "whatever"})
    assert login.status_code == 200, login.text

    # the cookie now authenticates the standard current-user endpoint
    me = await client.get("/api/users/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "boss@barsy.example"
    assert me.json()["role"] == "manager"


async def test_barsy_login_is_idempotent_upsert(client, mock_verify):
    """A second login with the same email updates the mirror in place (no duplicate user)."""
    mock_verify(_PROFILE)
    first = await client.post("/api/auth/barsy-login",
                              json={"username": "boss", "password": "whatever"})
    assert first.status_code == 200
    first_id = first.json()["id"]

    # role changes in Barsy (demoted to Служител) → mirror follows on next login
    mock_verify({**_PROFILE, "role_name": "Служител"})
    second = await client.post("/api/auth/barsy-login",
                               json={"username": "boss", "password": "whatever"})
    assert second.status_code == 200
    assert second.json()["id"] == first_id           # same local row (upsert, not insert)
    assert second.json()["role"] == "staff"          # role now mirrors the new Barsy role
    assert second.json()["is_superuser"] is False


async def test_barsy_login_bad_credentials_is_401(client, mock_verify):
    mock_verify(None)  # verify returns None → invalid credentials
    r = await client.post("/api/auth/barsy-login",
                          json={"username": "boss", "password": "wrong"})
    assert r.status_code == 401
    assert "darling_admin" not in client.cookies


async def test_barsy_login_barsy_down_is_503(client, mock_verify):
    mock_verify(RuntimeError("Barsy HTTP 500"))  # transport/availability problem
    r = await client.post("/api/auth/barsy-login",
                          json={"username": "boss", "password": "whatever"})
    assert r.status_code == 503
    assert "darling_admin" not in client.cookies


async def test_barsy_login_synthesizes_email_when_absent(client, mock_verify):
    """When Barsy has no email, the mirror keys on a synthesized VALID email (barsy.online —
    a real domain EmailStr accepts, not the reserved '.local') and login succeeds."""
    mock_verify({"user_id": 9, "role_name": "Оператор", "user_name": "packer1"})
    r = await client.post("/api/auth/barsy-login",
                          json={"username": "packer1", "password": "whatever"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "packer1@barsy.online"
    assert r.json()["role"] == "packer"
    assert r.json()["is_superuser"] is False
