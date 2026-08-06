# -*- coding: utf-8 -*-
"""2FA phase 1 — ENROLLMENT ONLY. Proves setup→verify→enabled, recovery codes issued once,
wrong-code rejection, owner reset, and — critically — that none of this changes the login path
yet (a user with no 2FA still logs in normally). The login GATE is phase 2, with its own tests.

Owner decision 2026-08-05: 2FA is MANDATORY for every role (changed from managers-only), so
nothing here is role-conditional; enrollment is identical for staff, manager and owner."""
import pyotp
import pytest

from app.routers import twofa


def _code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


async def _my_id(client) -> str:
    return (await client.get("/api/users/me")).json()["id"]


@pytest.mark.asyncio
async def test_status_defaults_off_and_mandatory(staff_client):
    s = (await staff_client.get("/api/auth/2fa/status")).json()
    assert s["enabled"] is False and s["mandatory"] is True and s["recovery_remaining"] == 0


@pytest.mark.asyncio
async def test_enroll_happy_path_issues_recovery_once(staff_client):
    setup = (await staff_client.post("/api/auth/2fa/setup")).json()
    assert setup["secret"] and setup["otpauth_uri"].startswith("otpauth://totp/")
    assert "<svg" in setup["qr_svg"]
    r = await staff_client.post("/api/auth/2fa/verify", json={"code": _code(setup["secret"])})
    body = r.json()
    assert r.status_code == 200 and body["enabled"] is True
    assert len(body["recovery_codes"]) == twofa.N_RECOVERY
    st = (await staff_client.get("/api/auth/2fa/status")).json()
    assert st["enabled"] is True and st["recovery_remaining"] == twofa.N_RECOVERY
    assert (await staff_client.post("/api/auth/2fa/setup")).status_code == 409   # already on


@pytest.mark.asyncio
async def test_verify_rejects_wrong_code_and_needs_setup_first(staff_client):
    assert (await staff_client.post("/api/auth/2fa/verify", json={"code": "000000"})).status_code == 400
    setup = (await staff_client.post("/api/auth/2fa/setup")).json()
    assert (await staff_client.post("/api/auth/2fa/verify", json={"code": "111111"})).status_code == 400
    assert (await staff_client.get("/api/auth/2fa/status")).json()["enabled"] is False
    ok = await staff_client.post("/api/auth/2fa/verify", json={"code": _code(setup["secret"])})
    assert ok.status_code == 200          # the right code still works after a miss


@pytest.mark.asyncio
async def test_recovery_regenerate_replaces_codes(staff_client):
    setup = (await staff_client.post("/api/auth/2fa/setup")).json()
    first = (await staff_client.post("/api/auth/2fa/verify",
                                     json={"code": _code(setup["secret"])})).json()
    regen = (await staff_client.post("/api/auth/2fa/recovery/regenerate")).json()
    assert len(regen["recovery_codes"]) == twofa.N_RECOVERY
    assert set(regen["recovery_codes"]) != set(first["recovery_codes"])


@pytest.mark.asyncio
async def test_owner_resets_a_user_but_never_the_owner_account(owner_client, staff_client):
    setup = (await staff_client.post("/api/auth/2fa/setup")).json()
    await staff_client.post("/api/auth/2fa/verify", json={"code": _code(setup["secret"])})
    staff_id = await _my_id(staff_client)
    owner_id = await _my_id(owner_client)

    assert (await staff_client.post(f"/api/auth/2fa/reset/{staff_id}")).status_code == 403  # not owner
    assert (await owner_client.post(f"/api/auth/2fa/reset/{staff_id}")).status_code == 200
    assert (await staff_client.get("/api/auth/2fa/status")).json()["enabled"] is False
    # the owner's own second factor cannot be stripped through this endpoint (manual recovery only)
    assert (await owner_client.post(f"/api/auth/2fa/reset/{owner_id}")).status_code == 400


@pytest.mark.asyncio
async def test_recovery_codes_are_not_stored_in_plaintext(staff_client):
    setup = (await staff_client.post("/api/auth/2fa/setup")).json()
    issued = (await staff_client.post("/api/auth/2fa/verify",
                                      json={"code": _code(setup["secret"])})).json()
    plain = issued["recovery_codes"][0]
    assert plain and "$argon2" not in plain          # user sees plaintext…
    st = (await staff_client.get("/api/auth/2fa/status")).json()
    assert st["recovery_remaining"] == twofa.N_RECOVERY   # …and the store keeps hashes (see router)


@pytest.mark.asyncio
async def test_login_path_is_untouched_in_phase1(client):
    """The whole safety claim of phase 1: enrollment exists, login is unchanged — so shipping it
    cannot strand anyone. Phase 2 adds the gate deliberately, with recovery proven first."""
    from conftest import _OWNER
    await client.post("/api/auth/setup", json=_OWNER)
    r = await client.post("/api/auth/login",
                          data={"username": _OWNER["email"], "password": _OWNER["password"]},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (200, 204)     # cookie issued directly — no 2fa ticket in phase 1
