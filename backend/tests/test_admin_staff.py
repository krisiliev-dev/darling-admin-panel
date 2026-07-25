# -*- coding: utf-8 -*-
"""Настройки / admin (/api/admin) — staff live in BARSY (source of truth); the panel
reads (Users_getlist) and creates (Users_create) them there. All of staff/roles/audit are
owner-only. We mock the shared barsy.call — Barsy generates + emails the password
(reset_password=1); the panel never sets or sees it.
"""
import pytest


_NEW_STAFF = {
    "username": "packer1", "first_name": "Иван", "last_name": "Петров",
    "email": "ivan@example.com", "role_id": 4,
}


# --- POST /api/admin/staff → Users_create body -------------------------------------
async def test_create_staff_body_sets_role_and_reset_password(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_create", 8001)
    r = await owner_client.post("/api/admin/staff", json=_NEW_STAFF)
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == 8001

    body = barsy_recorder.body_for("Users_create")
    user = body["user"]
    assert user["role_id"] == 4
    assert user["reset_password"] == 1          # Barsy generates + emails the password
    assert user["user_name"] == "packer1"
    assert user["email"] == "ivan@example.com"
    assert user["fname"] == "Иван"
    assert user["lname"] == "Петров"


async def test_create_staff_is_owner_only(staff_client, barsy_recorder):
    # a logged-in non-owner (not a superuser) must be refused before any ERP write
    r = await staff_client.post("/api/admin/staff", json=_NEW_STAFF)
    assert r.status_code == 403
    assert barsy_recorder.calls == []


async def test_create_staff_requires_auth(client, barsy_recorder):
    r = await client.post("/api/admin/staff", json=_NEW_STAFF)
    assert r.status_code == 401
    assert barsy_recorder.calls == []


# --- manager privilege guard: a manager may create only NON-privileged roles ----------
async def test_manager_cannot_create_privileged_role(manager_client, barsy_recorder):
    # a manager creating a Мениджър (role_id 1 → panel superuser) is refused before any ERP write
    r = await manager_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 1})
    assert r.status_code == 403, r.text
    assert barsy_recorder.calls == []


async def test_manager_can_create_nonprivileged_role(manager_client, barsy_recorder):
    barsy_recorder.set_return("Users_create", 9001)
    r = await manager_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 4})
    assert r.status_code == 201, r.text
    assert barsy_recorder.body_for("Users_create")["user"]["role_id"] == 4


async def test_owner_can_create_privileged_role(owner_client, barsy_recorder):
    # the owner bypasses the guard — may create any role, incl. a manager-level one
    barsy_recorder.set_return("Users_create", 9002)
    r = await owner_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 1})
    assert r.status_code == 201, r.text
    assert barsy_recorder.body_for("Users_create")["user"]["role_id"] == 1


# --- GET /api/admin/staff → Users_getlist mapping ----------------------------------
async def test_get_staff_maps_barsy_rows(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "full_name": "Шефа",
         "role_name": "Мениджър", "email": "boss@x.bg"},
        {"user_id": 2, "user_name": "gone", "role_name": "Служител", "delete_flag": 1},
        {"user_id": 3, "user_name": "ivan", "fname": "Иван", "lname": "Петров",
         "role_name": "Оператор", "email": "ivan@x.bg"},
    ])
    r = await owner_client.get("/api/admin/staff")
    assert r.status_code == 200, r.text
    staff = r.json()["staff"]
    # deleted row filtered out
    assert [s["user_id"] for s in staff] == [1, 3]
    assert staff[0] == {"user_id": 1, "user_name": "boss", "full_name": "Шефа",
                        "role": "Мениджър", "email": "boss@x.bg"}
    # full_name synthesized from fname/lname when absent
    assert staff[1]["full_name"] == "Иван Петров"
    assert staff[1]["role"] == "Оператор"


async def test_get_staff_is_owner_only(staff_client, barsy_recorder):
    r = await staff_client.get("/api/admin/staff")
    assert r.status_code == 403
    assert barsy_recorder.calls == []


# --- GET /api/admin/roles + /api/admin/audit (owner-only) --------------------------
async def test_roles_maps_barsy_rows(owner_client, barsy_recorder):
    barsy_recorder.set_return("Roles_getlist", [
        {"role_id": 1, "role_name": "Мениджър", "role_weight": 1},
        {"role_id": 9, "role_name": "Изтрита", "delete_flag": 1},
    ])
    r = await owner_client.get("/api/admin/roles")
    assert r.status_code == 200, r.text
    roles = r.json()["roles"]
    # Мениджър is a privileged (panel-superuser) role → flagged so the UI hides it from managers
    assert roles == [{"role_id": 1, "role_name": "Мениджър", "weight": 1, "privileged": True}]


async def test_roles_is_owner_only(staff_client, barsy_recorder):
    r = await staff_client.get("/api/admin/roles")
    assert r.status_code == 403


async def test_audit_is_owner_only(staff_client):
    r = await staff_client.get("/api/admin/audit")
    assert r.status_code == 403


async def test_audit_returns_entries_for_owner(owner_client):
    # the owner setup + staff creation already wrote audit rows; the log reads local (no Barsy)
    r = await owner_client.get("/api/admin/audit")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["entries"], list)
