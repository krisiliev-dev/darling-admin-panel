# -*- coding: utf-8 -*-
"""Настройки / admin (/api/admin) — staff live in BARSY (source of truth); the panel
reads (Users_getlist) and creates (Users_create) them there. All of staff/roles/audit are
owner-only. We mock the shared barsy.call — Barsy generates + emails the password
(reset_password=1); the panel never sets or sees it.
"""


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


async def test_manager_can_no_longer_create_staff_at_all(manager_client, barsy_recorder):
    """CONTRACT CHANGED 2026-07-30 (owner): creating accounts is SYSTEM administration, so it moved
    behind owner+sysadmin. A Мениджър previously could create non-privileged roles; now they can create
    none. Kept as an explicit test rather than deleted, so the narrowing is recorded as intended
    behaviour and a later "fix" that hands it back has to argue with a named decision."""
    r = await manager_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 4})
    assert r.status_code == 403, r.text
    assert barsy_recorder.calls == []            # nothing reached Barsy


async def test_a_granted_sysadmin_can_create_nonprivileged_but_not_privileged(
        manager_client, barsy_recorder):
    """With the capability granted, a sysadmin creates ordinary staff — but manager-level roles stay
    OWNER-only. System access must not become a back door to granting business authority."""
    from sqlalchemy import select

    from app.db import async_session_maker
    from app.models import User
    async with async_session_maker() as s:
        u = (await s.execute(select(User).where(User.email == "mgr@x.bg"))).scalar_one()  # type: ignore[arg-type]
        u.is_sysadmin = True
        await s.commit()
    barsy_recorder.set_return("Users_create", 9001)
    r = await manager_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 4})
    assert r.status_code == 201, r.text
    assert barsy_recorder.body_for("Users_create")["user"]["role_id"] == 4
    r = await manager_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 1})
    assert r.status_code == 403 and "собственикът" in r.json()["detail"]


async def test_owner_can_create_privileged_role(owner_client, barsy_recorder):
    # the owner bypasses the guard — may create any role, incl. a manager-level one
    barsy_recorder.set_return("Users_create", 9002)
    r = await owner_client.post("/api/admin/staff", json={**_NEW_STAFF, "role_id": 1})
    assert r.status_code == 201, r.text
    assert barsy_recorder.body_for("Users_create")["user"]["role_id"] == 1


# --- GET /api/admin/staff → Users_getlist mapping ----------------------------------
async def test_get_staff_resolves_role_id_by_joining_the_role_name(owner_client, barsy_recorder):
    """MEASURED shape: Users_getlist returns delete_flag, fname, full_name, lname, role_name,
    user_id, user_name — no role_id, no email, under any of five request variants
    (claude/BARSY_LIST_SHAPES_20260808.md). The previous fixture invented an `email` key and the
    previous assertion pinned role_id to None, so the Settings role dropdown never pre-selecting
    anyone's role had a green test behind it."""
    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "full_name": "Шефа", "role_name": "Мениджър"},
        {"user_id": 2, "user_name": "gone", "role_name": "Служител", "delete_flag": 1},
        {"user_id": 3, "user_name": "ivan", "fname": "Иван", "lname": "Петров",
         "role_name": "Оператор"},
    ])
    barsy_recorder.set_return("Roles_getlist", [
        {"role_id": 1, "role_name": "Мениджър", "role_weight": 1},
        {"role_id": 4, "role_name": "Оператор", "role_weight": 4},
        {"role_id": 5, "role_name": "Служител", "role_weight": 5},
    ])
    r = await owner_client.get("/api/admin/staff")
    assert r.status_code == 200, r.text
    staff = r.json()["staff"]
    # CONTRACT (2026-07-30): `delete_flag` is Barsy's „Блокиран", NOT a deletion — a real delete
    # removes the row. Blocked users stay VISIBLE and marked, or offboarding is one-way.
    assert [s["user_id"] for s in staff] == [1, 2, 3]
    assert staff[1]["blocked"] is True and staff[0]["blocked"] is False
    assert staff[0]["role"] == "Мениджър" and staff[0]["role_id"] == 1
    assert staff[2]["role"] == "Оператор" and staff[2]["role_id"] == 4
    assert staff[2]["full_name"] == "Иван Петров"      # synthesized from fname/lname
    # Barsy's users list carries no email at all, so this is "" — asserted so that the day a
    # detail surface is wired up, this test says so instead of quietly passing.
    assert staff[0]["email"] == ""


async def test_an_unknown_role_name_leaves_the_id_unset(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "role_name": "Роля-която-я-няма"}])
    barsy_recorder.set_return("Roles_getlist", [{"role_id": 1, "role_name": "Мениджър"}])
    staff = (await owner_client.get("/api/admin/staff")).json()["staff"]
    assert staff[0]["role"] == "Роля-която-я-няма"     # the name still shows
    assert staff[0]["role_id"] is None


async def test_a_duplicated_role_name_resolves_to_nothing(owner_client, barsy_recorder):
    """Barsy does not stop an owner naming two roles alike. Picking the first would show someone
    a role they do not hold, and the id is what a save would write back — so an ambiguous name
    resolves to None and the UI shows its placeholder."""
    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "role_name": "Оператор"},
        {"user_id": 2, "user_name": "ivan", "role_name": "Мениджър"}])
    barsy_recorder.set_return("Roles_getlist", [
        {"role_id": 4, "role_name": "Оператор"},
        {"role_id": 7, "role_name": "Оператор"},          # same name, different id
        {"role_id": 1, "role_name": "Мениджър"}])
    staff = (await owner_client.get("/api/admin/staff")).json()["staff"]
    assert staff[0]["role_id"] is None, "an ambiguous name must not be guessed at"
    assert staff[1]["role_id"] == 1, "the unambiguous ones still resolve"


async def test_a_deleted_role_does_not_lend_its_name(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "role_name": "Стара"}])
    barsy_recorder.set_return("Roles_getlist", [
        {"role_id": 9, "role_name": "Стара", "delete_flag": 1}])
    staff = (await owner_client.get("/api/admin/staff")).json()["staff"]
    assert staff[0]["role_id"] is None


async def test_the_staff_list_survives_roles_being_unreadable(owner_client, barsy_recorder):
    """Fail OPEN: this is presentation, not money. Losing the id must not lose the page — and the
    degradation is visible, because the UI shows its "no id" placeholder."""
    def boom(_body):
        raise RuntimeError("Barsy е зает")

    barsy_recorder.set_return("Users_getlist", [
        {"user_id": 1, "user_name": "boss", "role_name": "Мениджър"}])
    barsy_recorder.set_return("Roles_getlist", boom)
    r = await owner_client.get("/api/admin/staff")
    assert r.status_code == 200, r.text
    staff = r.json()["staff"]
    assert staff[0]["user_name"] == "boss"
    assert staff[0]["role"] == "Мениджър"
    assert staff[0]["role_id"] is None


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


# ---------------------------------------------------------------- edit / block (users_edit) ---
_BARSY_USER = {
    "user_id": 16, "user_name": "zz-test", "fname": "Тест", "lname": "Тестов",
    "email": "zz@example.invalid", "phone": "0888", "role_id": "3", "delete_flag": 0,
    "barsys": ["1"], "max_discount": "0", "session_timeout": "30", "picture": "",
}


async def test_edit_reads_then_sends_the_WHOLE_form(owner_client, barsy_recorder):
    """Barsy's save does not merge — a partial body BLANKS every omitted field (captured 2026-07-30).
    So changing a role must round-trip the entire user. This test is the guard against someone
    'simplifying' this into {values:{role_id}} and silently wiping names, emails and permissions."""
    barsy_recorder.set_return("Users_get", _BARSY_USER)
    barsy_recorder.set_return("users_edit", 16)
    r = await owner_client.patch("/api/admin/staff/16", json={"role_id": 4})
    assert r.status_code == 200, r.text
    body = barsy_recorder.body_for("users_edit")
    assert body["action_type"] == "save" and body["id"] == 16
    v = body["values"]
    assert v["role_id"] == "4"                       # the one change
    assert v["user_id"] == "16"                      # id rides INSIDE values too
    for k in ("user_name", "fname", "lname", "email", "phone", "barsys", "session_timeout"):
        assert v[k] == _BARSY_USER[k], f"{k} was dropped — a partial save would blank it"


async def test_block_sets_delete_flag_and_never_calls_users_delete(owner_client, barsy_recorder):
    """`users_delete` is a HARD delete (row gone, Users_get 404s afterwards), which would orphan the
    audit trail's authors. Offboarding must be the soft `delete_flag:1` — Barsy's own „Блокиран"."""
    barsy_recorder.set_return("Users_get", _BARSY_USER)
    barsy_recorder.set_return("users_edit", 16)
    r = await owner_client.patch("/api/admin/staff/16", json={"blocked": True})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("users_edit")["values"]["delete_flag"] == 1
    assert "users_delete" not in barsy_recorder.operations
    assert "Users_delete" not in barsy_recorder.operations


async def test_unblock_clears_the_flag(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_get", {**_BARSY_USER, "delete_flag": 1})
    barsy_recorder.set_return("users_edit", 16)
    r = await owner_client.patch("/api/admin/staff/16", json={"blocked": False})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("users_edit")["values"]["delete_flag"] == 0


async def test_edit_of_a_missing_user_is_404_and_writes_nothing(owner_client, barsy_recorder):
    barsy_recorder.set_return("Users_get", {"error": "Не е намерен потребител с ID: 99"})
    r = await owner_client.patch("/api/admin/staff/99", json={"role_id": 4})
    assert r.status_code == 404
    assert "users_edit" not in barsy_recorder.operations


async def test_empty_edit_is_rejected(owner_client, barsy_recorder):
    r = await owner_client.patch("/api/admin/staff/16", json={})
    assert r.status_code == 400
    assert barsy_recorder.calls == []


async def test_sysadmin_cannot_promote_someone_to_manager(manager_client, barsy_recorder):
    """A granted sysadmin may re-role staff, but handing out manager-level roles stays OWNER-only —
    otherwise system access becomes a route to granting business authority."""
    from sqlalchemy import select

    from app.db import async_session_maker
    from app.models import User
    async with async_session_maker() as s:
        u = (await s.execute(select(User).where(User.email == "mgr@x.bg"))).scalar_one()  # type: ignore[arg-type]
        u.is_sysadmin = True
        await s.commit()
    r = await manager_client.patch("/api/admin/staff/16", json={"role_id": 1})
    assert r.status_code == 403 and "собственикът" in r.json()["detail"]
    assert barsy_recorder.calls == []


async def test_staff_cannot_edit_users(staff_client, barsy_recorder):
    r = await staff_client.patch("/api/admin/staff/16", json={"blocked": True})
    assert r.status_code in (401, 403)
    assert barsy_recorder.calls == []
