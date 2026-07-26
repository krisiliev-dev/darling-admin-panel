# -*- coding: utf-8 -*-
"""ОРЗД / GDPR data-subject requests. PII lives mainly in SHOPIFY (the customer system of record,
which has its own data-request + erasure flow). The panel holds only its OWN operational data about a
customer — the refusal/risk history (OrderOutcome) and any nekorekten report ids. This tool lets the
owner see and erase THAT, and retract any report we published, on a data-subject erasure request.
Shopify erasure is triggered separately in Shopify. Owner/manager-only + confirm + audit."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .. import nekorekten
from ..audit import log_action
from ..db import get_async_session
from ..models import OrderOutcome, User
from ..users import current_owner

router = APIRouter(prefix="/api/gdpr", tags=["gdpr"])


@router.get("/lookup")
async def lookup(
    phone: str = Query(..., description="телефон на клиента"),
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """What panel data we hold about this customer (by normalized phone). Read-only."""
    ph = nekorekten.normalize_phone(phone)
    rows = (await session.execute(
        select(OrderOutcome).where(OrderOutcome.phone_norm == ph).order_by(OrderOutcome.id.desc()))).scalars().all()
    # a READ of personal data — audit it (GDPR: access to PII must be traceable)
    await log_action(session, actor, "gdpr.lookup", target=ph, detail=f"count={len(rows)}")
    return {
        "phone": ph,
        "outcomes": [{
            "id": r.id, "order_name": r.order_name, "outcome": r.outcome,
            "cod_amount": r.cod_amount, "recorded_ts": r.recorded_ts.isoformat() if r.recorded_ts else None,
            "reported_to_nekorekten": bool(r.nekorekten_report_id),
        } for r in rows],
        "count": len(rows),
        "note": "Данните за клиента (име/имейл/адрес/поръчки) живеят в Shopify — изтрий ги и там.",
    }


class EraseBody(BaseModel):
    phone: str = Field(max_length=32)
    confirm: bool = False


@router.post("/erase")
async def erase(
    body: EraseBody,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Erase the panel's own data for a customer AND retract any nekorekten report we published about
    them (publishing their data was our processing, so we withdraw it). Owner-only + confirm + audit.
    Does NOT touch Shopify/Barsy — Shopify erasure is a separate action there."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    ph = nekorekten.normalize_phone(body.phone)
    rows = (await session.execute(
        select(OrderOutcome).where(OrderOutcome.phone_norm == ph))).scalars().all()
    retracted = 0
    failed = []
    erased = 0
    for r in rows:
        if r.nekorekten_report_id:
            res = await run_in_threadpool(nekorekten.delete_report, r.nekorekten_report_id)
            if not res.get("ok"):
                # KEEP the row — it holds the report id we need to retry the retraction. Deleting it
                # would orphan the published PII on nekorekten with no way to withdraw it.
                failed.append(r.order_name or str(r.id))
                continue
            retracted += 1
        await session.delete(r)
        erased += 1
    if erased:
        await session.commit()
    await log_action(session, actor, "gdpr.erase", target=ph,
                     detail=f"erased={erased} reports_retracted={retracted} retraction_failed={len(failed)}")
    note = "Изтрито от панела. Изтрий данните на клиента и в Shopify (система на записа)."
    if failed:
        note = (f"Внимание: {len(failed)} сигнал(а) в НЕкоректен не бяха оттеглени — записите са запазени, "
                f"за да опиташ пак. " + note)
    return {"ok": True, "phone": ph, "erased": erased, "reports_retracted": retracted,
            "retraction_failed": failed, "note": note}
