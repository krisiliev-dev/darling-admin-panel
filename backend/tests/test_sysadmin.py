# -*- coding: utf-8 -*-
"""The sysadmin capability (owner decision 2026-07-30): SYSTEM-level surfaces — write gates,
integrations/webhook config, audit, logs, GDPR tools, user administration — move behind owner+sysadmin,
while Barsy MANAGERS keep every business function (orders, pricing, Склад, intake).

This deliberately NARROWS the 2026-07-20 decision that managers hold full write, so these tests are the
record of what was intended: they assert both halves — that a manager is now refused system surfaces, AND
that a manager still reaches business ones. Asserting only the first half would let a later "fix" hand
managers system access back, or strip their business access, without a test noticing.
"""
from sqlalchemy import select

from app.db import async_session_maker
from app.models import User

SYSTEM_GETS = ["/api/admin/staff", "/api/admin/roles", "/api/admin/audit",
               "/api/admin/panel-users", "/api/logs", "/api/logs/actions"]


async def _grant(email: str, value: bool = True) -> None:
    """Set is_sysadmin directly (the endpoint is owner-only and tested separately)."""
    async with async_session_maker() as s:
        u = (await s.execute(select(User).where(User.email == email))).scalar_one()  # type: ignore[arg-type]
        u.is_sysadmin = value
        await s.commit()


# ---------------------------------------------------------------- the narrowing ---
async def test_manager_is_refused_system_surfaces(manager_client):
    for path in SYSTEM_GETS:
        r = await manager_client.get(path)
        assert r.status_code == 403, f"{path} let a manager in: {r.status_code}"
        assert "системен администратор" in r.json()["detail"]


async def test_manager_keeps_business_surfaces(manager_client, barsy_recorder):
    """The other half of the decision. A Мениджър still has full business authority — if this ever
    starts failing, the narrowing has over-reached and managers can't do their jobs."""
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": 1, "depot_name": "Основен"}])
    assert (await manager_client.get("/api/store-ops/depots")).status_code == 200
    r = await manager_client.post("/api/store-ops/writeoff", json={
        "lines": [{"article_id": 5, "amount": 1}], "reason_id": 1})
    assert r.status_code == 200, r.text          # gate off → dry-run, but ALLOWED


async def test_owner_reaches_system_surfaces_without_any_grant(owner_client, barsy_recorder):
    """`current_sysadmin` accepts role=='owner' precisely so applying this narrowing cannot lock the
    owner out of their own panel before anyone has been granted the capability."""
    barsy_recorder.set_return("Users_getlist", [{"user_id": 1, "user_name": "kris"}])
    barsy_recorder.set_return("Roles_getlist", [{"role_id": 1, "role_name": "Мениджър"}])
    for path in SYSTEM_GETS:
        assert (await owner_client.get(path)).status_code == 200, path


async def test_granted_manager_reaches_system_surfaces(manager_client, barsy_recorder):
    barsy_recorder.set_return("Users_getlist", [])
    await _grant("mgr@x.bg")
    assert (await manager_client.get("/api/admin/audit")).status_code == 200
    assert (await manager_client.get("/api/admin/staff")).status_code == 200


async def test_revoking_closes_access_again(manager_client, barsy_recorder):
    barsy_recorder.set_return("Users_getlist", [])
    await _grant("mgr@x.bg")
    assert (await manager_client.get("/api/admin/audit")).status_code == 200
    await _grant("mgr@x.bg", False)
    assert (await manager_client.get("/api/admin/audit")).status_code == 403


async def test_staff_and_packer_are_refused(staff_client, packer_client):
    for c in (staff_client, packer_client):
        assert (await c.get("/api/admin/audit")).status_code == 403


# ---------------------------------------------------------------- granting the capability ---
# NB fixture ORDER matters: `staff_client` pulls in `owner_client`, whose /setup 409s if any user
# already exists — so the owner-derived fixture must be requested BEFORE `manager_client`.
async def test_only_the_owner_may_grant(staff_client, manager_client):
    """A MANAGER must not be able to grant it — they'd hand themselves exactly the access this design
    withholds. `current_owner` would have allowed this, since Barsy managers map to superuser; the
    endpoint uses `current_owner_strict` for that reason. Same escalation shape the July audit found."""
    for c in (manager_client, staff_client):
        r = await c.post("/api/admin/sysadmin", json={"email": "mgr@x.bg", "grant": True})
        assert r.status_code == 403, r.text
    async with async_session_maker() as s:
        u = (await s.execute(select(User).where(User.email == "mgr@x.bg"))).scalar_one_or_none()  # type: ignore[arg-type]
    assert u is None or not u.is_sysadmin       # nothing was granted


async def test_owner_grants_and_revokes(owner_client, manager_client):
    r = await owner_client.post("/api/admin/sysadmin", json={"email": "mgr@x.bg", "grant": True})
    assert r.status_code == 200 and r.json()["is_sysadmin"] is True
    r = await owner_client.post("/api/admin/sysadmin", json={"email": "mgr@x.bg", "grant": False})
    assert r.status_code == 200 and r.json()["is_sysadmin"] is False


async def test_grant_to_unknown_email_is_404(owner_client):
    r = await owner_client.post("/api/admin/sysadmin",
                                json={"email": "nobody@x.bg", "grant": True})
    assert r.status_code == 404


async def test_owner_row_is_refused_rather_than_silently_no_op(owner_client):
    """role=='owner' already passes every system guard, so writing is_sysadmin on that row would be a
    no-op that LOOKS like it did something. Say so instead."""
    me = (await owner_client.get("/api/users/me")).json()
    r = await owner_client.post("/api/admin/sysadmin", json={"email": me["email"], "grant": False})
    assert r.status_code == 400 and "винаги има системен достъп" in r.json()["detail"]


# ---------------------------------------------------------------- it must survive a Barsy login ---
async def test_capability_survives_a_barsy_relogin(manager_client, monkeypatch, barsy_recorder):
    """THE reason this is a column and not a 4th `role` value: barsy_login overwrites role AND
    is_superuser from the Barsy role map on every sign-in. If the capability lived in `role`, it would
    be silently wiped the next time that person logged in — access that evaporates without a trace."""
    barsy_recorder.set_return("Users_getlist", [])
    await _grant("mgr@x.bg")
    r = await manager_client.post("/api/auth/barsy-login", json={"username": "mgr", "password": "x"})
    assert r.status_code in (200, 204), r.text
    assert r.json()["is_sysadmin"] is True                       # exposed for the frontend
    assert (await manager_client.get("/api/admin/staff")).status_code == 200


async def test_self_service_patch_cannot_grant_the_capability(staff_client):
    """`UserUpdate` omits is_sysadmin on purpose: fastapi-users' /me PATCH strips only
    {id,is_superuser,is_active,is_verified}, so any field we declared would pass straight through."""
    r = await staff_client.patch("/api/users/me", json={"is_sysadmin": True, "full_name": "X"})
    assert r.status_code in (200, 400)
    me = (await staff_client.get("/api/users/me")).json()
    assert me["is_sysadmin"] is False
    assert (await staff_client.get("/api/admin/audit")).status_code == 403
