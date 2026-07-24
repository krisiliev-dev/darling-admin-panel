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
from ..core import barsy
from ..db import get_async_session
from ..models import AuditLog, User
from ..upstream import upstream
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/staff")
def staff(user: User = Depends(current_owner)):
    """Staff from Barsy (Users_getlist) — Barsy is the source of truth for accounts + roles."""
    with upstream("Barsy потребители"):
        rows = barsy.call("Users_getlist", {})
    return {"staff": [
        {"user_id": r.get("user_id"), "user_name": r.get("user_name", ""),
         "full_name": r.get("full_name") or f"{r.get('fname','')} {r.get('lname','')}".strip(),
         "role": r.get("role_name", ""), "email": r.get("email", "")}
        for r in (rows or []) if not r.get("delete_flag")]}


class NewStaff(BaseModel):
    username: str
    first_name: str
    last_name: str = ""
    email: EmailStr
    role_id: int


@router.post("/staff", status_code=status.HTTP_201_CREATED)
async def create_staff(
    req: NewStaff,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a staff account IN BARSY (Users_create). Barsy generates + emails the password
    (reset_password) — the panel never sets or sees it. Role must be an existing Barsy role."""
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
def roles(user: User = Depends(current_owner)):
    """Barsy roles (read-only — role definitions are managed in Barsy)."""
    with upstream("Barsy роли"):
        rows = barsy.call("Roles_getlist", {})
    return {"roles": [{"role_id": r.get("role_id"), "role_name": r.get("role_name", ""),
                       "weight": r.get("role_weight")} for r in (rows or [])
                      if not r.get("delete_flag")]}


@router.get("/audit")
async def audit(
    user: User = Depends(current_owner),
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
