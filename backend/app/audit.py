# -*- coding: utf-8 -*-
"""Append-only audit trail. Two sources feed it:
  • log_action(...) — SEMANTIC events with rich detail, called from handlers (intake.approve, …).
  • record_request(...) — EVERY mutating HTTP request (POST/PUT/PATCH/DELETE), via middleware, so
    nothing a user does on the panel goes unlogged even where a semantic call is missing.
"""
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, User


async def log_action(
    session: AsyncSession, actor: User | None, action: str,
    target: str = "", detail: str = "",
) -> None:
    session.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "",
        action=action, target=target, detail=detail,
    ))
    await session.commit()


_AUDIENCE = ["fastapi-users:auth"]


async def record_request(request, status: int) -> None:
    """Log one mutating request. Actor is decoded from the auth cookie (no DB hit); email is resolved
    on read via the User join. Best-effort — auditing must never break a request."""
    from fastapi_users.jwt import decode_jwt
    from .config import get_settings
    from .db import async_session_maker

    actor_id = None
    token = request.cookies.get("darling_admin")
    if token:
        try:
            sub = decode_jwt(token, get_settings().jwt_secret, _AUDIENCE).get("sub")
            actor_id = _uuid.UUID(sub) if sub else None
        except Exception:
            actor_id = None
    async with async_session_maker() as session:
        session.add(AuditLog(actor_id=actor_id, actor_email="",
                             action=f"http:{request.method}", target=str(request.url.path)[:255],
                             detail=f"status={status}"))
        await session.commit()
