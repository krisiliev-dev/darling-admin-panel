# -*- coding: utf-8 -*-
"""Append-only audit helper."""
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
