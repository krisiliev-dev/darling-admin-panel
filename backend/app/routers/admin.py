# -*- coding: utf-8 -*-
"""Настройки / admin — staff (from BARSY Users, the source of truth), the panel audit log, and
system status. Staff live in Barsy; the panel reads/creates them there. The audit log is
panel-only (who did what IN the panel) and stays local."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..barsy_auth import map_role
from ..core import barsy
from ..db import get_async_session
from ..models import AuditLog, User
from ..upstream import upstream
from ..users import current_owner_strict, current_sysadmin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/staff")
def staff(user: User = Depends(current_sysadmin)):
    """Staff from Barsy (Users_getlist) — Barsy is the source of truth for accounts + roles.

    Blocked users are LISTED, not filtered out. `delete_flag` is Barsy's „Блокиран" checkbox, not a
    deletion (a real deletion removes the row entirely), so hiding those rows would make offboarding
    one-way: nobody could see who is blocked, let alone un-block them. They come back marked instead."""
    with upstream("Barsy потребители"):
        rows = barsy.call("Users_getlist", {})
    return {"staff": [
        {"user_id": r.get("user_id"), "user_name": r.get("user_name", ""),
         "full_name": r.get("full_name") or f"{r.get('fname','')} {r.get('lname','')}".strip(),
         "role": r.get("role_name", ""), "role_id": r.get("role_id"),
         "email": r.get("email", ""), "blocked": bool(r.get("delete_flag"))}
        for r in (rows or [])]}


class NewStaff(BaseModel):
    username: str
    first_name: str
    last_name: str = ""
    email: EmailStr
    role_id: int


@router.post("/staff", status_code=status.HTTP_201_CREATED)
async def create_staff(
    req: NewStaff,
    actor: User = Depends(current_sysadmin),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a staff account IN BARSY (Users_create). Barsy generates + emails the password
    (reset_password) — the panel never sets or sees it. Role must be an existing Barsy role.
    PRIVILEGE GUARD: a sysadmin may create only non-privileged roles (Служител/Оператор); creating a
    manager-level role (Мениджър/Управител, i.e. another panel superuser with full business authority)
    stays OWNER-only — system access must not be a route to granting business authority."""
    _, privileged = map_role({"role_id": req.role_id})
    if privileged and actor.role != "owner":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Само собственикът може да създава мениджърски роли. Избери Служител или Оператор.")
    body = {"user": {
        "user_name": req.username.strip(), "fname": req.first_name.strip(),
        "lname": req.last_name.strip(), "email": str(req.email),
        "role_id": req.role_id, "reset_password": 1,
    }}
    with upstream("Barsy създаване на потребител"):
        new_id = await run_in_threadpool(barsy.call, "Users_create", body)
    await log_action(session, actor, "staff.create", target=req.username,
                     detail=f"barsy_user_id={new_id} role_id={req.role_id}")
    return {"ok": True, "user_id": new_id,
            "note": "Barsy изпраща имейл с парола на служителя."}


@router.get("/roles")
def roles(user: User = Depends(current_sysadmin)):
    """Barsy roles (read-only — role definitions are managed in Barsy). `privileged` flags the
    manager-level roles that only the owner may assign (the UI hides those for managers)."""
    with upstream("Barsy роли"):
        rows = barsy.call("Roles_getlist", {})
    return {"roles": [{"role_id": r.get("role_id"), "role_name": r.get("role_name", ""),
                       "weight": r.get("role_weight"),
                       "privileged": map_role({"role_id": r.get("role_id")})[1]}
                      for r in (rows or []) if not r.get("delete_flag")]}


class StaffEdit(BaseModel):
    """Change ONE thing about a Barsy user: their role, or whether they're blocked."""
    role_id: int | None = None
    blocked: bool | None = None      # Barsy's `delete_flag` — the UI's „Блокиран" checkbox


@router.patch("/staff/{user_id}")
async def edit_staff(
    user_id: int,
    req: StaffEdit,
    actor: User = Depends(current_sysadmin),
    session: AsyncSession = Depends(get_async_session),
):
    """Re-role or block a Barsy user → `users_edit` (lowercase, `action_type:"save"`), the envelope
    captured from Barsy's own UI 2026-07-30.

    🚨 READ-MODIFY-WRITE IS MANDATORY. The capture shows the ENTIRE form travels on a save: a partial
    body does not merge, it BLANKS every field it omits — name, email, phone, `barsys`, discount limits,
    session timeout. So we fetch the user, change exactly the one field asked for, and send it all back.

    🚨 BLOCKING IS NOT DELETION. `users_delete {id}` is a HARD delete (verified: the row vanishes from
    Users_getlist under every filter, and Users_get 404s afterwards) — so it is deliberately NOT exposed
    anywhere in the panel. Offboarding sets `delete_flag: 1`, which is what Barsy's own „Блокиран"
    checkbox does. A destroyed user row would orphan the audit trail: the panel's log records WHO did
    each ERP write, and Н-18 traceability needs that person to stay nameable.

    Manager-level roles stay OWNER-only, same as create_staff — system access must not become a way to
    grant business authority."""
    if req.role_id is None and req.blocked is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма какво да се промени.")
    if req.role_id is not None:
        _, privileged = map_role({"role_id": req.role_id})
        if privileged and actor.role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Само собственикът може да дава мениджърска роля.")
    with upstream("Barsy потребител"):
        current = await run_in_threadpool(barsy.call, "Users_get", {"user_id": user_id})
    if not isinstance(current, dict) or not current.get("user_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Потребителят не е намерен в Barsy.")
    # the whole form, then the single change on top — never a partial body
    values = {k: v for k, v in current.items() if k != "picture"}
    values["user_id"] = str(user_id)
    if req.role_id is not None:
        values["role_id"] = str(req.role_id)
    if req.blocked is not None:
        values["delete_flag"] = 1 if req.blocked else 0
    body = {"action_type": "save", "active_struct_id": "eStructForm_1", "id": user_id,
            "values": values}
    with upstream("Barsy запис на потребител"):
        res = await run_in_threadpool(barsy.call, "users_edit", body)
    what = []
    if req.role_id is not None:
        what.append(f"role_id={req.role_id}")
    if req.blocked is not None:
        what.append("блокиран" if req.blocked else "отблокиран")
    await log_action(session, actor, "staff.edit", target=str(user_id), detail=", ".join(what))
    return {"ok": True, "user_id": user_id, "changed": what, "result": res}


# ---------------------------------------------------------------- sysadmin capability ---
class SysadminGrant(BaseModel):
    email: EmailStr
    grant: bool


@router.get("/panel-users")
async def panel_users(actor: User = Depends(current_sysadmin),
                      session: AsyncSession = Depends(get_async_session)):
    """Panel-side accounts (the local mirror), with who holds the sysadmin capability. Distinct from
    /staff, which lists BARSY users — a person can exist in Barsy and have never signed in here."""
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return {"users": [
        {"email": u.email, "full_name": u.full_name, "role": u.role,
         "is_sysadmin": bool(u.is_sysadmin), "is_active": bool(u.is_active),
         "is_superuser": bool(u.is_superuser)}
        for u in rows]}


@router.post("/sysadmin")
async def set_sysadmin(
    req: SysadminGrant,
    actor: User = Depends(current_owner_strict),
    session: AsyncSession = Depends(get_async_session),
):
    """Grant/revoke the sysadmin capability. OWNER-ONLY — and note `current_owner_strict`, not
    `current_owner`: the latter accepts any superuser, which includes Barsy managers, so using it here
    would let a manager grant themselves exactly the access this capability exists to withhold. That is
    the same privilege-escalation shape the July audit found in create_staff.

    The owner's own row is refused: role=='owner' already passes every system guard, so 'revoking' it
    would be a no-op that reads as if it did something."""
    target = (await session.execute(
        select(User).where(User.email == req.email.lower()))).scalar_one_or_none()  # type: ignore[arg-type]  # SQLAlchemy/fastapi-users stub gap, not a bug
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Няма такъв потребител в панела. Той трябва да е влизал поне веднъж.")
    if target.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Собственикът винаги има системен достъп — няма какво да се променя.")
    target.is_sysadmin = bool(req.grant)
    await session.commit()
    await log_action(session, actor, "sysadmin.grant" if req.grant else "sysadmin.revoke",
                     target=target.email, detail=f"role={target.role}")
    return {"ok": True, "email": target.email, "is_sysadmin": bool(target.is_sysadmin)}


@router.get("/audit")
async def audit(
    user: User = Depends(current_sysadmin),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
):
    """Panel audit trail — who did what in the panel (login, ERP writes, labels, POs…)."""
    rows = (await session.execute(
        select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit))).scalars().all()
    return {"entries": [
        {"ts": a.ts.isoformat() if a.ts else "", "actor": a.actor_email,
         "action": a.action, "target": a.target, "detail": a.detail}
        for a in rows]}
