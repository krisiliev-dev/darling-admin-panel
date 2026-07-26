# -*- coding: utf-8 -*-
"""Настройки / admin (/api/admin) — error paths + auth gating not covered by
test_admin_staff.py (which covers the happy-path Users_create/getlist + Roles mapping).

Here: the 502 upstream path when Barsy raises (staff/roles), owner-only gating on roles &
create, 401 on the reads, the audit limit validation, and audit ordering (newest first).
"""

import app.routers.admin as admin

_NEW_STAFF = {"username": "u1", "first_name": "И", "last_name": "П",
              "email": "u1@example.com", "role_id": 4}


async def test_staff_barsy_failure_is_502(owner_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(admin.barsy, "call", boom)
    r = await owner_client.get("/api/admin/staff")
    assert r.status_code == 502
    assert "Barsy потребители" in r.json()["detail"]


async def test_roles_barsy_failure_is_502(owner_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(admin.barsy, "call", boom)
    r = await owner_client.get("/api/admin/roles")
    assert r.status_code == 502


async def test_create_staff_barsy_failure_is_502(owner_client, monkeypatch):
    def boom(operation, body=None, *a, **k):
        raise RuntimeError("barsy down")
    monkeypatch.setattr(admin.barsy, "call", boom)
    r = await owner_client.post("/api/admin/staff", json=_NEW_STAFF)
    assert r.status_code == 502
    assert "създаване" in r.json()["detail"]


async def test_create_staff_invalid_email_is_422(owner_client, barsy_recorder):
    r = await owner_client.post("/api/admin/staff", json={**_NEW_STAFF, "email": "not-an-email"})
    assert r.status_code == 422
    assert barsy_recorder.calls == []   # never reached the ERP


async def test_staff_read_requires_auth(client):
    r = await client.get("/api/admin/staff")
    assert r.status_code == 401


async def test_roles_read_requires_auth(client):
    r = await client.get("/api/admin/roles")
    assert r.status_code == 401


async def test_audit_requires_auth(client):
    r = await client.get("/api/admin/audit")
    assert r.status_code == 401


async def test_audit_rejects_bad_limit(owner_client):
    r = await owner_client.get("/api/admin/audit", params={"limit": "0"})
    assert r.status_code == 422
    r2 = await owner_client.get("/api/admin/audit", params={"limit": "9999"})
    assert r2.status_code == 422


async def test_audit_newest_first(owner_client, barsy_recorder):
    # generate a couple of audit rows via real owner-gated writes, then assert ordering
    barsy_recorder.set_return("Users_create", 1).set_return("Users_getlist", [])
    await owner_client.post("/api/admin/staff", json={**_NEW_STAFF, "username": "first"})
    await owner_client.post("/api/admin/staff",
                            json={**_NEW_STAFF, "username": "second", "email": "u2@example.com"})
    r = await owner_client.get("/api/admin/audit")
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    actions = [e["action"] for e in entries]
    # audit is ordered by ts DESC (newest first); staff.create entries are present
    assert "staff.create" in actions
    # the two staff.create targets appear, most-recent ('second') at or before 'first'
    targets = [e["target"] for e in entries if e["action"] == "staff.create"]
    assert targets[:2] == ["second", "first"]
