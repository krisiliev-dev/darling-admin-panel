# -*- coding: utf-8 -*-
"""COD-refuser risk — combine our OWN refusal history with a nekorekten.com lookup into a per-order
flag. The flag is advisory: staff decide whether to hold the order for confirmation / require prepay.
Never an automatic reject (GDPR Art. 22). See app/nekorekten.py + models.OrderOutcome."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .. import nekorekten
from ..audit import log_action
from ..config import get_settings
from ..core import connector
from ..db import get_async_session
from ..ids import numeric_oid
from ..models import OrderOutcome, User
from ..users import current_active_user, current_owner, require_roles

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _level(internal_refusals: int, external_reports) -> str:
    """low / medium / high from own history + external reports. Conservative: one signal = medium,
    two refusals or several external reports = high. Tuned to flag, not to punish."""
    ext = external_reports or 0
    if internal_refusals >= 2 or ext >= 3:
        return "high"
    if internal_refusals >= 1 or ext >= 1:
        return "medium"
    return "low"


@router.get("/lookup")
async def lookup(
    phone: str = Query("", description="телефон на клиента"),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Risk snapshot for a phone: our own delivered/refused counts + nekorekten report count + a
    combined level + a manual deep-link. Safe to call per order (nekorekten free tier is limited)."""
    ph = nekorekten.normalize_phone(phone)
    internal_refusals = internal_delivered = 0
    if ph:
        rows = (await session.execute(
            select(OrderOutcome.outcome, func.count()).where(OrderOutcome.phone_norm == ph)
            .group_by(OrderOutcome.outcome))).all()
        counts = {o: c for o, c in rows}
        internal_refusals = counts.get("refused", 0)
        internal_delivered = counts.get("delivered", 0)
    # nekorekten call is blocking network I/O — off the event loop
    ext = await run_in_threadpool(nekorekten.check_phone, ph) if ph else {"enabled": nekorekten.enabled(), "reports": None}
    return {
        "phone": ph,
        "internal_refusals": internal_refusals,
        "internal_delivered": internal_delivered,
        "nekorekten": ext,                       # {enabled, reports, error?}
        "risk_level": _level(internal_refusals, ext.get("reports")),
        "lookup_url": nekorekten.search_url(ph),
    }


class OutcomeBody(BaseModel):
    phone: str = Field(max_length=32)
    outcome: str = Field(pattern="^(delivered|refused)$")
    address: str = Field("", max_length=255)
    order_id: str = Field("", max_length=64)
    order_name: str = Field("", max_length=64)
    cod_amount: float = Field(0.0, ge=0, le=1_000_000)


@router.post("/outcome", status_code=status.HTTP_201_CREATED)
async def record_outcome(
    body: OutcomeBody,
    actor: User = Depends(require_roles("staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    """Record how a delivery ended (delivered/refused) so repeat refusers surface next time. This is
    what feeds the internal side of the risk score; courier tracking can call it automatically later."""
    ph = nekorekten.normalize_phone(body.phone)
    row = OrderOutcome(
        phone_norm=ph, address_norm=body.address.strip().lower(),
        order_id=body.order_id, order_name=body.order_name, outcome=body.outcome,
        cod_amount=body.cod_amount, recorded_by=actor.email)
    session.add(row)
    await session.commit()
    await log_action(session, actor, "risk.outcome", target=ph, detail=body.outcome)
    # a REFUSED COD (RTO) means the goods came back → storno the Barsy sale to restore stock. Gated:
    # dry-run until COD_BARSY_POST_LIVE is on. Never blocks recording the outcome.
    barsy = await _cod_storno_on_refusal(body) if body.outcome == "refused" else None
    return {"ok": True, "phone": ph, "outcome": body.outcome, "barsy": barsy}


async def _cod_storno_on_refusal(body: "OutcomeBody"):
    if not body.order_id:
        return None
    live = get_settings().cod_barsy_post_live
    try:
        # the connector keys its order log on the BARE numeric id; a GID (what the panel/Shopify use)
        # never matches → the storno silently no-ops. Normalise here. See app/ids.py.
        return await run_in_threadpool(connector.cod_refused_to_barsy, numeric_oid(body.order_id),
                                       "COD отказан (RTO)", live)
    except Exception as e:
        return {"error": str(e)[:140]}


class ReportBody(BaseModel):
    phone: str = Field(max_length=32)
    text: str = Field(min_length=5, max_length=2000)  # factual reason — required
    first_name: str = Field("", max_length=100)
    last_name: str = Field("", max_length=100)
    order_id: str = Field("", max_length=64)          # link to our outcome row, if any
    confirm: bool = False


@router.post("/report")
async def report_customer(
    body: ReportBody,
    actor: User = Depends(current_owner),  # PUBLISHING a person's data — owner/manager only, deliberate
    session: AsyncSession = Depends(get_async_session),
):
    """Submit a refuser report to nekorekten.com (publishes to their public DB). EXPLICIT human action
    only (confirm required), factual grounds only, never automatic. Stores the returned report id on
    our OrderOutcome so it can be retracted. GDPR: legitimate interest (fraud prevention) — must be in
    the privacy policy; see COMPLIANCE.md."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    if not nekorekten.enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "НЕкоректен не е конфигуриран.")
    res = await run_in_threadpool(
        nekorekten.report, body.text, body.phone, body.first_name, body.last_name, None)
    if not res.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"НЕкоректен отказа: {res.get('error')}")
    ph = nekorekten.normalize_phone(body.phone)
    rid = res.get("report_id")
    # H1: ALWAYS persist the report id so a published report is retractable — even when reported
    # straight from the shipment tool with no prior outcome row. Upsert onto the matching row, else
    # create one. Losing the id would leave a person's data published with no server-side handle.
    q = (select(OrderOutcome).where(OrderOutcome.order_id == body.order_id) if body.order_id
         else select(OrderOutcome).where(OrderOutcome.phone_norm == ph)).order_by(OrderOutcome.id.desc())
    row = (await session.execute(q.limit(1))).scalar_one_or_none()
    if row is None:
        row = OrderOutcome(phone_norm=ph, order_id=body.order_id, outcome="refused",
                           recorded_by=actor.email)
        session.add(row)
    if rid:
        row.nekorekten_report_id = str(rid)
    await session.commit()
    await log_action(session, actor, "risk.report", target=ph, detail=f"report_id={rid}")
    return {"ok": True, "report_id": rid}


@router.get("/reports")
async def outstanding_reports(
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Every report we filed to nekorekten that is still live (has an id), so the owner can retract
    one even without the original session — closes the H1 'unretractable report' gap."""
    rows = (await session.execute(select(OrderOutcome).where(
        OrderOutcome.nekorekten_report_id != "").order_by(OrderOutcome.id.desc()))).scalars().all()
    return {"reports": [
        {"phone": r.phone_norm, "report_id": r.nekorekten_report_id,
         "order_name": r.order_name, "recorded_by": r.recorded_by} for r in rows]}


class RetractBody(BaseModel):
    report_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False


@router.post("/report/retract")
async def retract_report(
    body: RetractBody,
    actor: User = Depends(current_owner),
    session: AsyncSession = Depends(get_async_session),
):
    """Withdraw a report we filed (mistake / resolved). Deletes it on nekorekten and clears the id
    from our OrderOutcome. Owner/manager-only + confirm."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужно е потвърждение.")
    # M4: only retract reports WE filed — never delete an arbitrary/foreign id
    rows = (await session.execute(
        select(OrderOutcome).where(OrderOutcome.nekorekten_report_id == body.report_id))).scalars().all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма подаден от нас сигнал с този номер.")
    res = await run_in_threadpool(nekorekten.delete_report, body.report_id)
    if not res.get("ok"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"НЕкоректен отказа: {res.get('error')}")
    for row in rows:
        row.nekorekten_report_id = ""
    if rows:
        await session.commit()
    await log_action(session, actor, "risk.report_retract", target=body.report_id, detail="")
    return {"ok": True}
