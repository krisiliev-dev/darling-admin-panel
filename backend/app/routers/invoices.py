# -*- coding: utf-8 -*-
"""Фактури за издаване — the B2B VAT-invoice worklist.

WHY this exists as a manual worklist rather than an automation: Barsy's API **cannot issue a фактура**.
There is no `Invoices_create` (the whole Invoices category is read-only), and Barsy's auto-issue flag
only fires for **VPOS** payments — our наложен платеж never triggers it. The only working path is the
admin UI: Документи → Фактури → „Нова фактура по сметка(и)", picking the closed sale account.

So the split is: the CONNECTOR knows WHICH orders requested a фактура (captured at checkout, read back
from Barsy so it survives a container recreate); this router pairs that with the panel-owned record of
which have been ISSUED. A B2B buyer is legally owed an invoice — the one thing we must never do is let
the request vanish silently, which is exactly what happened before (it was discarded on the live path).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import connector
from ..db import get_async_session
from ..models import IssuedInvoice, User
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

_COMPANY_FIELDS = ("name", "eik", "vat", "mol", "city", "address")


def _company(raw) -> dict:
    """Normalise the captured company block: always the same keys, so the UI can render a stable block
    and visibly flag what's missing (a фактура with a blank ЕИК is useless to the buyer)."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: str(raw.get(k) or "").strip() for k in _COMPANY_FIELDS}


@router.get("")
async def list_requests(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Orders that asked for a фактура, split into pending (to issue) and issued (for the record).
    Each pending row carries the copy-ready company block + the Barsy `account_id` staff need for
    „Нова фактура по сметка(и)"."""
    try:
        requests = await run_in_threadpool(connector.list_invoice_requests)
    except AttributeError:
        # the connector read isn't deployed yet — say so plainly instead of pretending there's no work
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Списъкът с фактури не е наличен (connector.list_invoice_requests липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy недостъпен: {str(e)[:160]}")

    done = {r.order_id: r for r in (await session.execute(select(IssuedInvoice))).scalars().all()}
    pending, issued = [], []
    for r in (requests or []):
        if not isinstance(r, dict):
            continue
        oid = str(r.get("order_id") or "")
        comp = _company(r.get("company"))
        row = {
            "order_id": oid, "order_ref": r.get("order_ref") or oid,
            "account_id": str(r.get("account_id") or ""), "amount": r.get("amount"),
            "company": comp,
            # what's missing blocks a legally-valid invoice — surface it rather than letting staff
            # discover it halfway through the Barsy form
            "missing": [k for k in ("name", "eik") if not comp[k]],
        }
        rec = done.get(oid)
        if rec:                                   # a human issued it and recorded the number here
            issued.append({**row, "invoice_number": rec.invoice_number,
                           "issued_by": rec.issued_by, "auto": False,
                           "issued_at": rec.issued_at.isoformat() if rec.issued_at else ""})
        elif r.get("issued"):
            # the connector ISSUED IT AUTOMATICALLY (Invoices_save at finalise). It must never appear as
            # pending: this screen is the exception queue, and showing an already-issued order as "to do"
            # invites a SECOND фактура for the same sale — a real accounting error, not just noise.
            issued.append({**row, "invoice_number": str(r.get("invoice_number") or ""),
                           "issued_by": "автоматично", "auto": True,
                           "issued_at": str(r.get("issued_ts") or r.get("requested_ts") or "")})
        else:
            pending.append(row)
    return {"pending": pending, "issued": issued,
            "pending_count": len(pending), "issued_count": len(issued)}


class MarkIssued(BaseModel):
    invoice_number: str = Field("", max_length=32)
    account_id: str = Field("", max_length=32)
    order_ref: str = Field("", max_length=64)
    company: str = Field("", max_length=255)
    confirm: bool = False


@router.post("/{order_id}/issued")
async def mark_issued(
    order_id: str, body: MarkIssued,
    actor: User = Depends(require_roles("owner", "manager", "staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Record that the фактура was issued in Barsy, with its number. Panel-side only — we never touch
    the Barsy document (we can't). `order_id` is unique, so a double-click can't double-record; a second
    attempt reports the number already on file instead of overwriting it."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Потвърди, че фактурата е издадена в Barsy.")
    if not body.invoice_number.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Въведи номера на издадената фактура (за да остане в отчета).")
    # the connector may have issued it automatically since the page was loaded — refuse rather than
    # record a manual number over an existing фактура (that would hide a double-issue, not prevent one)
    try:
        auto = next((r for r in (await run_in_threadpool(connector.list_invoice_requests) or [])
                     if isinstance(r, dict) and str(r.get("order_id") or "") == order_id
                     and r.get("issued")), None)
    except Exception:
        auto = None                                  # connector unavailable → fall through to the local check
    if auto:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Фактурата вече е издадена автоматично (№ {auto.get('invoice_number') or '—'}). "
            f"Не издавай втора за същата поръчка.")
    existing = (await session.execute(
        select(IssuedInvoice).where(IssuedInvoice.order_id == order_id))).scalar_one_or_none()
    if existing:
        return {"ok": True, "already": True, "invoice_number": existing.invoice_number,
                "message": f"Вече е отбелязана като издадена (№ {existing.invoice_number})."}
    session.add(IssuedInvoice(
        order_id=order_id, order_ref=body.order_ref or order_id, account_id=body.account_id,
        invoice_number=body.invoice_number.strip(), company=body.company, issued_by=actor.email))
    await session.commit()
    await log_action(session, actor, "invoice.issued", target=body.order_ref or order_id,
                     detail=f"№ {body.invoice_number.strip()} · сметка {body.account_id}")
    return {"ok": True, "invoice_number": body.invoice_number.strip()}
