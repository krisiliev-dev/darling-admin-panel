# -*- coding: utf-8 -*-
"""ОРЗД / GDPR data-subject requests. PII lives mainly in SHOPIFY (the customer system of record,
which has its own data-request + erasure flow). The panel holds only its OWN operational data about a
customer — the refusal/risk history (OrderOutcome) with any nekorekten report ids, plus the abandoned-
cart mirror (Checkout: email/phone/name from the checkout webhooks). This tool lets the owner see and
erase ALL of that, and retract any report we published, on a data-subject erasure request.
Shopify erasure is triggered separately in Shopify. Owner/manager-only + confirm + audit.

Every panel table that stores customer PII MUST be added to both collect_ and erase_customer_data
below — an erasure that misses a table is an Art.17 failure that still reports success."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .. import nekorekten
from ..audit import log_action
from ..db import get_async_session
from ..models import Checkout, OrderOutcome, User
from ..users import current_owner

router = APIRouter(prefix="/api/gdpr", tags=["gdpr"])


# ── reusable core (called by the HTTP endpoints below AND the Shopify GDPR webhooks) ──────────────
def _norm_email(raw: str | None) -> str:
    return (raw or "").strip().lower()


async def _matching_checkouts(session: AsyncSession, ph: str, em: str) -> list[Checkout]:
    """Cart-mirror rows belonging to this data subject. Checkout.phone holds the RAW Shopify value
    ("+359 88 812 3456", "0888123456", …), so an SQL equality on the normalized phone would miss —
    we narrow to rows that carry any contact PII and compare normalized in Python. The mirror is
    small (live + recently abandoned carts), so the scan is cheap. Empty ph/em never match, or a
    digit-less erasure request would sweep every anonymous cart."""
    if not ph and not em:
        return []
    cands = (await session.execute(select(Checkout).where(
        or_(Checkout.phone != "", Checkout.email != "")))).scalars().all()
    out = []
    for c in cands:
        if ph and c.phone and nekorekten.normalize_phone(c.phone) == ph:
            out.append(c)
        elif em and _norm_email(c.email) == em:
            out.append(c)
    return out


async def collect_customer_data(session: AsyncSession, phone: str, email: str = "") -> dict:
    """The panel data we hold about a customer, by normalized phone (+ optional email for the cart
    mirror, which Shopify often has an email but no phone for). No audit (caller decides)."""
    ph = nekorekten.normalize_phone(phone)
    em = _norm_email(email)
    # phone_norm == "" matches every phone-less record → only query with a real phone
    rows = (await session.execute(select(OrderOutcome)
                                  .where(OrderOutcome.phone_norm == ph)
                                  .order_by(OrderOutcome.id.desc()))).scalars().all() if ph else []
    carts = await _matching_checkouts(session, ph, em)
    return {
        "phone": ph,
        "email": em,
        "outcomes": [{
            "id": r.id, "order_name": r.order_name, "outcome": r.outcome,
            "cod_amount": r.cod_amount, "recorded_ts": r.recorded_ts.isoformat() if r.recorded_ts else None,
            "reported_to_nekorekten": bool(r.nekorekten_report_id),
        } for r in rows],
        # the cart mirror holds real PII (email/phone/name) → it must appear in an Art.15 export
        "checkouts": [{
            "token": c.token, "email": c.email, "phone": c.phone, "name": c.name,
            "total_price": c.total_price, "currency": c.currency, "item_count": c.item_count,
            "completed": c.completed,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in carts],
        "outcomes_count": len(rows),
        "checkouts_count": len(carts),
        "count": len(rows) + len(carts),
    }


async def erase_customer_data(session: AsyncSession, phone: str, actor: User | None = None,
                              email: str = "") -> dict:
    """Erase the panel's own data for a customer (refusal history + cart mirror) + retract any
    nekorekten report we published. Audited (actor may be None for a system/webhook-driven erasure).
    Does NOT touch Shopify/Barsy."""
    ph = nekorekten.normalize_phone(phone)
    em = _norm_email(email)
    if not ph and not em:
        # a digit-less input (e.g. "n/a") normalizes to "" — refuse, or we'd match+erase EVERY
        # phone-less OrderOutcome row (our records are keyed by phone_norm) and every anonymous cart.
        await log_action(session, actor, "gdpr.erase.skipped_no_phone", target=str(phone)[:32])
        return {"phone": "", "erased": 0, "checkouts_erased": 0, "reports_retracted": 0,
                "retraction_failed": [], "skipped": "no usable phone"}
    # only query the phone-keyed history with a REAL phone — phone_norm == "" matches every
    # phone-less row, so an email-only request must never reach it.
    rows = (await session.execute(
        select(OrderOutcome).where(OrderOutcome.phone_norm == ph))).scalars().all() if ph else []
    retracted = 0
    failed: list[str] = []
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
    carts = await _matching_checkouts(session, ph, em)
    for c in carts:
        await session.delete(c)
    if erased or carts:
        await session.commit()
    await log_action(session, actor, "gdpr.erase", target=ph or em,
                     detail=f"erased={erased} checkouts={len(carts)} reports_retracted={retracted} "
                            f"retraction_failed={len(failed)}")
    return {"phone": ph, "erased": erased, "checkouts_erased": len(carts),
            "reports_retracted": retracted, "retraction_failed": failed}


@router.get("/lookup")
async def lookup(
    phone: str = Query(..., description="телефон на клиента"),
    email: str = Query("", description="имейл (по избор — кошниците често имат само имейл)"),
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """What panel data we hold about this customer (by normalized phone + optional email). Read-only."""
    data = await collect_customer_data(session, phone, email)
    # a READ of personal data — audit it (GDPR: access to PII must be traceable)
    await log_action(session, actor, "gdpr.lookup", target=data["phone"] or data["email"],
                     detail=f"count={data['count']}")
    data["note"] = "Данните за клиента (име/имейл/адрес/поръчки) живеят в Shopify — изтрий ги и там."
    return data


class EraseBody(BaseModel):
    phone: str = Field(max_length=32)
    email: str = Field("", max_length=255)
    confirm: bool = False


@router.post("/erase")
async def erase(
    body: EraseBody,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Erase the panel's own data for a customer AND retract any nekorekten report we published about
    them. Owner-only + confirm + audit. Does NOT touch Shopify/Barsy — Shopify erasure is separate."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    res = await erase_customer_data(session, body.phone, actor, email=body.email)
    note = "Изтрито от панела (история + кошници). Изтрий данните на клиента и в Shopify (система на записа)."
    if res["retraction_failed"]:
        note = (f"Внимание: {len(res['retraction_failed'])} сигнал(а) в НЕкоректен не бяха оттеглени — "
                f"записите са запазени, за да опиташ пак. " + note)
    return {"ok": True, **res, "note": note}
