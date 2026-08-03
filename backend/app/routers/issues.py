# -*- coding: utf-8 -*-
"""Проблеми — the processing-issues surface (owner 2026-07-27). Lists connector/webhook anomalies the
receiver recorded (unmatched SKU, needs-review, exception…) so staff act on them instead of them dying in
logs. Read + acknowledge (resolve); the rows themselves are written by app.issues from the webhook path."""
import datetime as _dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..db import get_async_session
from ..models import ProcessingIssue, User
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/issues", tags=["issues"])


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _row(i: ProcessingIssue) -> dict:
    try:
        detail = json.loads(i.detail or "{}")
    except Exception:
        detail = {"raw": i.detail}
    return {
        "id": i.id, "created_at": _iso(i.created_at), "last_seen": _iso(i.last_seen),
        "source": i.source, "topic": i.topic, "order_ref": i.order_ref,
        "severity": i.severity, "kind": i.kind, "summary": i.summary, "detail": detail,
        "count": i.count, "resolved": i.resolved,
        "resolved_at": _iso(i.resolved_at), "resolved_by": i.resolved_by,
    }


@router.get("")
async def list_issues(
    resolved: bool | None = Query(None, description="filter; omit for all (open first)"),
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Issues, open first then newest. `open_count` drives the nav badge + Home strip."""
    q = (select(ProcessingIssue)
         .order_by(ProcessingIssue.resolved.asc(), ProcessingIssue.created_at.desc())
         .limit(limit))
    if resolved is not None:
        q = q.where(ProcessingIssue.resolved.is_(resolved))
    rows = (await session.execute(q)).scalars().all()
    open_count = (await session.execute(
        select(func.count()).select_from(ProcessingIssue)
        .where(ProcessingIssue.resolved.is_(False)))).scalar_one()
    return {"issues": [_row(i) for i in rows], "open_count": open_count}


class ResolveAllIn(BaseModel):
    reason: str = ""


@router.post("/resolve-all")
async def resolve_all(
    req: ResolveAllIn,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Acknowledge every OPEN issue at once — owner-only, and only with a written reason.

    Bulk-clearing is a real need (a batch of test-order noise) and a real risk (an issue is the
    only place a failure is visible), so the reason is mandatory: it is what carries the knowledge
    into the audit log before the rows stop being visible. Nothing is deleted — the rows stay,
    marked resolved, with who cleared them and why."""
    reason = (req.reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "\u041d\u0430\u043f\u0438\u0448\u0438 \u0437\u0430\u0449\u043e \u0433\u0438 \u043e\u0442\u0431\u0435\u043b\u044f\u0437\u0432\u0430\u0448 \u043a\u0430\u0442\u043e \u0440\u0435\u0448\u0435\u043d\u0438 \u2014 \u043f\u0440\u0438\u0447\u0438\u043d\u0430\u0442\u0430 \u043e\u0441\u0442\u0430\u0432\u0430 \u0432 \u0434\u043d\u0435\u0432\u043d\u0438\u043a\u0430.")
    rows = (await session.execute(
        select(ProcessingIssue).where(ProcessingIssue.resolved.is_(False)))).scalars().all()
    now = _dt.datetime.now(_dt.UTC)
    for i in rows:
        i.resolved = True
        i.resolved_at = now
        i.resolved_by = actor.email or ""
    if rows:
        await session.commit()
    await log_action(session, actor, "issues.resolve_all", target=str(len(rows)), detail=reason)
    return {"ok": True, "resolved": len(rows), "reason": reason}


@router.post("/{issue_id}/resolve")
async def resolve_issue(
    issue_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Acknowledge an issue (once the data's fixed + the order re-fired). Idempotent."""
    issue = await session.get(ProcessingIssue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такъв проблем.")
    if not issue.resolved:
        issue.resolved = True
        issue.resolved_at = func.now()
        issue.resolved_by = user.email or ""
        await session.commit()
    return {"ok": True, "id": issue_id}
