# -*- coding: utf-8 -*-
"""Заявки — per-supplier purchase requests (outbound orders to suppliers). Its own page.

Top of the page = insight cards (highest-ordered / highest-earning / needs-reordering products) that
fast-add into a supplier's заявка. Below = a list of suppliers each with an open заявка (item count +
total); opening one shows its lines (qty ±, remove) and a search over that supplier's catalogue.
Send by email (Gmail SMTP) or export to CSV/Excel. Panel-owned staging — Barsy records STOCK only
at receiving (Доставки), so a заявка here is a purchasing document, not a Barsy write.

Data foundation: supplier_codes.jsonl (product → supplier(s) with their code + delivery price) and
the historical sales seed (units/revenue/margin) for the insight cards.
"""
import csv
import io
import re
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy, barsy_write_live
from ..db import get_async_session
from ..models import SupplierPref, User, Zayavka, ZayavkaLine
from ..services.product_seed import products as _products
from ..services.supplier_catalog import supplier_catalog, suppliers_for
from ..users import current_active_user, current_owner
from .intake_delivery import _norm

router = APIRouter(prefix="/api/zayavki", tags=["zayavki"])

_PERIOD_MONTHS = 54   # the historical seed spans ~2021-11 → 2026-07

# supplier catalogue now lives in the service layer; keep the private aliases for the call sites here
_supplier_catalog = supplier_catalog
_suppliers_for = suppliers_for


@lru_cache(maxsize=1)
def _sales_by_code() -> dict:
    """{code -> seed sales row} for enriching cards / suggested qty."""
    return {str(p.get("code")): p for p in _products() if p.get("code")}


def _suggested_qty(units) -> int:
    """A gentle reorder hint from lifetime units over the seed's ~54-month span (≈2 months' cover)."""
    try:
        monthly = float(units or 0) / _PERIOD_MONTHS
    except (TypeError, ValueError):
        monthly = 0
    return max(1, round(monthly * 2))


# ------------------------------------------------------------------ insight cards ---
def _card_product(p: dict) -> dict:
    code = str(p.get("code") or "")
    sups = list(_suppliers_for(code).keys())
    return {
        "code": code, "name": p.get("name", ""),
        "supplier": (p.get("supplier") or (sups[0] if sups else "")),
        "suppliers": sups, "units": p.get("units", 0), "rev": p.get("rev", 0.0),
        "marg": p.get("marg", 0.0), "stock": p.get("stock", 0), "dead": bool(p.get("dead")),
        "suggested_qty": _suggested_qty(p.get("units")),
    }


@router.get("/no-supplier")
def no_supplier(user: User = Depends(current_active_user), limit: int = Query(50, ge=1, le=500)):
    """Products with NO supplier in the mapping — they can't be ordered until one is assigned.
    A data-quality nudge (surfaced as a count on the page)."""
    by_code = _supplier_catalog()["by_code"]
    orphans = [p for p in _products()
               if not p.get("discontinued") and str(p.get("code") or "") not in by_code]
    return {"count": len(orphans),
            "products": [{"code": str(p.get("code")), "name": p.get("name", ""),
                          "units": p.get("units", 0)} for p in orphans[:limit]]}


@router.get("/insights")
def insights(user: User = Depends(current_active_user), limit: int = Query(8, ge=1, le=30)):
    """Cards of products worth ordering — each expandable to a list with fast-add."""
    prods = [p for p in _products() if not p.get("discontinued")]
    live = [p for p in prods if not p.get("dead")]
    top_ordered = sorted(live, key=lambda p: p.get("units") or 0, reverse=True)[:limit]
    top_earners = sorted(live, key=lambda p: p.get("rev") or 0.0, reverse=True)[:limit]
    # low stock among still-selling products = the actionable "reorder" card
    low_stock = sorted([p for p in live if (p.get("stock") or 0) <= 3 and (p.get("units") or 0) > 0],
                       key=lambda p: p.get("units") or 0, reverse=True)[:limit]
    return {"cards": [
        {"key": "reorder", "title": "Да се поръча (ниска наличност)",
         "subtitle": "продаваеми, но с малко на склад", "products": [_card_product(p) for p in low_stock]},
        {"key": "top_ordered", "title": "Най-поръчвани",
         "subtitle": "най-много продадени бройки", "products": [_card_product(p) for p in top_ordered]},
        {"key": "top_earners", "title": "Най-печеливши",
         "subtitle": "най-голям оборот", "products": [_card_product(p) for p in top_earners]},
    ]}


# ------------------------------------------------------------------ helpers ---
def _line_out(ln: ZayavkaLine) -> dict:
    return {"id": ln.id, "code": ln.code, "supplier_code": ln.supplier_code, "name": ln.name,
            "qty": ln.qty, "delivery_price": ln.delivery_price, "barcode": ln.barcode,
            "line_total": round(ln.qty * ln.delivery_price, 2)}


async def _lines(session: AsyncSession, zid: int) -> list[ZayavkaLine]:
    return list((await session.execute(
        select(ZayavkaLine).where(ZayavkaLine.zayavka_id == zid).order_by(ZayavkaLine.id))).scalars().all())


async def _detail(session: AsyncSession, z: Zayavka) -> dict:
    lines = await _lines(session, z.id)
    total = round(sum(ln.qty * ln.delivery_price for ln in lines), 2)
    return {"id": z.id, "supplier": z.supplier, "supplier_id": z.supplier_id,
            "supplier_email": z.supplier_email, "status": z.status, "notes": z.notes,
            "created_by": z.created_by, "sent_at": z.sent_at.isoformat() if z.sent_at else None,
            "sent_via": z.sent_via, "items": len(lines), "total": total,
            "lines": [_line_out(ln) for ln in lines]}


async def _open_draft(session: AsyncSession, supplier: str) -> Zayavka | None:
    return (await session.execute(
        select(Zayavka).where(Zayavka.supplier == supplier, Zayavka.status == "draft"))).scalars().first()


# ------------------------------------------------------------------ suppliers list ---
async def _prefs_map(session: AsyncSession) -> dict:
    return {p.supplier: p for p in (await session.execute(select(SupplierPref))).scalars().all()}


@router.get("/suppliers")
async def suppliers(user: User = Depends(current_active_user),
                    session: AsyncSession = Depends(get_async_session)):
    """Every supplier we can order from (from the catalogue) + its open заявка stats + prefs."""
    cat = _supplier_catalog()["by_supplier"]
    drafts = {z.supplier: z for z in (await session.execute(
        select(Zayavka).where(Zayavka.status == "draft"))).scalars().all()}
    sent = dict((s, t) for s, t in (await session.execute(
        select(Zayavka.supplier, func.max(Zayavka.sent_at)).where(Zayavka.status == "sent")
        .group_by(Zayavka.supplier))).all())
    prefs = await _prefs_map(session)
    out = []
    for sup, prods in cat.items():
        z = drafts.get(sup)
        items, total = 0, 0.0
        if z:
            lines = await _lines(session, z.id)
            items = len(lines)
            total = round(sum(ln.qty * ln.delivery_price for ln in lines), 2)
        pref = prefs.get(sup)
        min_order = pref.min_order if pref else 0.0
        out.append({"supplier": sup, "catalog_count": len(prods),
                    "open_id": z.id if z else None, "items": items, "total": total,
                    "last_sent": sent.get(sup).isoformat() if sent.get(sup) else None,
                    "email": pref.email if pref else "", "min_order": min_order,
                    "lead_days": pref.lead_days if pref else 0,
                    "website": pref.website if pref else "", "phone": pref.phone if pref else "",
                    "below_min": bool(min_order and total and total < min_order)})
    out.sort(key=lambda s: (-s["items"], -s["catalog_count"]))
    return {"suppliers": out}


class PrefPatch(BaseModel):
    email: str | None = None
    min_order: float | None = None
    lead_days: int | None = None
    website: str | None = None
    phone: str | None = None
    notes: str | None = None
    official_name: str | None = None
    bulstat: str | None = None
    vat_num: str | None = None
    mol: str | None = None
    address: str | None = None


def _norm_url(u: str) -> str:
    """Normalise a supplier website to an http(s) URL, or "" if it can't be. Guards the
    open-in-new-tab button against javascript:/file:/other schemes."""
    u = (u or "").strip()
    if not u:
        return ""
    low = u.lower()
    if low.startswith(("http://", "https://")):
        return u
    # any explicit scheme (scheme:… — incl. javascript:, file:, data:) is rejected; only bare
    # host/path input gets an https:// prefix
    if re.match(r"^[a-z][a-z0-9+.\-]*:", low):
        return ""
    return "https://" + u


@router.patch("/supplier-prefs/{supplier}")
async def set_pref(supplier: str, req: PrefPatch, actor: User = Depends(current_active_user),
                   session: AsyncSession = Depends(get_async_session)):
    """Persist a supplier's email / minimum-order / lead time / website / phone (survives across заявки)."""
    p = await session.get(SupplierPref, supplier)
    if not p:
        p = SupplierPref(supplier=supplier)
        session.add(p)
    if req.email is not None:
        p.email = req.email.strip()
    if req.min_order is not None:
        p.min_order = max(0.0, req.min_order)
    if req.lead_days is not None:
        p.lead_days = max(0, req.lead_days)
    if req.website is not None:
        p.website = _norm_url(req.website)
    if req.phone is not None:
        p.phone = req.phone.strip()[:64]
    if req.notes is not None:
        p.notes = req.notes
    if req.official_name is not None:
        p.official_name = req.official_name.strip()[:255]
    if req.bulstat is not None:
        p.bulstat = req.bulstat.strip()[:32]
    if req.vat_num is not None:
        p.vat_num = req.vat_num.strip()[:32]
    if req.mol is not None:
        p.mol = req.mol.strip()[:255]
    if req.address is not None:
        p.address = req.address.strip()[:500]
    await session.commit()
    return {"supplier": supplier, "email": p.email, "min_order": p.min_order,
            "lead_days": p.lead_days, "website": p.website, "phone": p.phone, "notes": p.notes,
            "official_name": p.official_name, "bulstat": p.bulstat, "vat_num": p.vat_num,
            "mol": p.mol, "address": p.address, "barsy_supplier_id": p.barsy_supplier_id}


def _supplier_data(p: SupplierPref, supplier: str) -> dict:
    """Build a Barsy SupplierData body from a panel SupplierPref. Barsy's SupplierData has no
    website/phone fields (mol/email are single-value), so website + phone fold into notes — mirroring
    agent-pipeline/push_suppliers.py. supplier_name falls back to the display name if no legal name."""
    extra = []
    if p.website:
        extra.append(f"Уебсайт: {p.website}")
    if p.phone:
        extra.append(f"Тел: {p.phone}")
    notes = "; ".join([n for n in [p.notes.strip() if p.notes else "", *extra] if n])
    data = {"supplier_name": (p.official_name or supplier).strip()}
    if p.bulstat:
        data["bulstat"] = p.bulstat
    if p.vat_num:
        data["vat_num"] = p.vat_num
    if p.mol:
        data["mol"] = p.mol            # single value — a ';'-joined multi would 500 (Barsy gotcha)
    if p.address:
        data["address"] = p.address
    if p.email:
        data["email"] = p.email
    if notes:
        data["notes"] = notes
    return data


@router.post("/supplier/{supplier}/push-barsy")
async def push_supplier_to_barsy(supplier: str, actor: User = Depends(current_owner),
                                 session: AsyncSession = Depends(get_async_session)):
    """Reflect a supplier's identity to Barsy (the ERP system-of-record): Suppliers_update if we
    already know its Barsy id, else Suppliers_create (and store the returned id). Owner-only, audited,
    and gated by BARSY_WRITE_LIVE (dry-run pre-launch)."""
    p = await session.get(SupplierPref, supplier)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма запазени данни за този доставчик.")
    data = _supplier_data(p, supplier)
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True,
                "action": "update" if p.barsy_supplier_id else "create",
                "supplier_id": p.barsy_supplier_id, "would_send": data,
                "note": "Barsy запис е изключен (BARSY_WRITE_LIVE=0) — нищо не е записано."}
    if p.barsy_supplier_id:
        await run_in_threadpool(barsy.call, "Suppliers_update",
                                {"supplier_id": p.barsy_supplier_id, "supplier": data})
        action, sid = "update", p.barsy_supplier_id
    else:
        out = await run_in_threadpool(barsy.call, "Suppliers_create", {"supplier": data})
        sid = int(out) if isinstance(out, int) or (isinstance(out, str) and out.isdigit()) else (
            out.get("supplier_id") if isinstance(out, dict) else None)
        if sid:
            p.barsy_supplier_id = int(sid)
            await session.commit()
        action = "create"
    await log_action(session, actor, "supplier.push_barsy", target=supplier,
                     detail=f"action={action} supplier_id={sid}")
    return {"ok": True, "action": action, "supplier_id": sid, "sent": data}


@router.get("/supplier/{supplier}/history")
async def supplier_history(supplier: str, user: User = Depends(current_active_user),
                           session: AsyncSession = Depends(get_async_session)):
    """Previous (sent) заявки for one supplier — for the detail view's „предишни заявки“ button."""
    rows = (await session.execute(
        select(Zayavka).where(Zayavka.supplier == supplier, Zayavka.status == "sent")
        .order_by(Zayavka.sent_at.desc()).limit(50))).scalars().all()
    out = []
    for z in rows:
        lines = await _lines(session, z.id)
        out.append({"id": z.id, "items": len(lines),
                    "total": round(sum(ln.qty * ln.delivery_price for ln in lines), 2),
                    "sent_at": z.sent_at.isoformat() if z.sent_at else None,
                    "sent_via": z.sent_via or ""})
    return {"supplier": supplier, "zayavki": out}


@router.get("/supplier/{supplier}/catalog")
async def supplier_catalog(supplier: str, q: str = Query(""), limit: int = Query(50, ge=1, le=200),
                           user: User = Depends(current_active_user)):
    """Products this supplier offers (their catalogue), searchable by name or code — for adding."""
    rows = _supplier_catalog()["by_supplier"].get(supplier, [])
    nq = _norm(q)
    if nq:
        rows = [r for r in rows if nq in _norm(r["name"]) or nq in _norm(r["code"]) or nq in _norm(r["ref"])]
    sales = _sales_by_code()
    out = []
    for r in rows[:limit]:
        s = sales.get(r["code"], {})
        out.append({"code": r["code"], "supplier_code": r["ref"], "name": s.get("name") or r["name"],
                    "delivery_price": r["dprice"], "units": s.get("units", 0),
                    "dead": bool(s.get("dead"))})   # застояла стока — "не поръчвай" guard
    return {"products": out, "total": len(rows)}


# ------------------------------------------------------------------ add / edit / remove ---
class AddRequest(BaseModel):
    code: str
    supplier: str = ""          # if empty and the product has >1 supplier → 409 with choices
    qty: float = 1.0
    name: str = ""


@router.post("/add")
async def add_to_zayavka(req: AddRequest, actor: User = Depends(current_active_user),
                         session: AsyncSession = Depends(get_async_session)):
    """Add a product to a supplier's open заявка. Resolves the supplier (prompts if ambiguous),
    creates the draft if needed, and bumps qty if the product is already in the cart."""
    code = req.code.strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Липсва код.")
    supmap = _suppliers_for(code)
    supplier = req.supplier.strip()
    if not supplier:
        if len(supmap) == 1:
            supplier = next(iter(supmap))
        elif len(supmap) > 1:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail={"need_supplier": True, "choices": list(supmap.keys()), "code": code})
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Този продукт няма свързан доставчик — задай доставчик.")
    info = supmap.get(supplier, {})
    z = await _open_draft(session, supplier)
    if not z:
        z = Zayavka(supplier=supplier, status="draft", created_by=actor.email)
        session.add(z)
        await session.flush()
    existing = next((ln for ln in await _lines(session, z.id) if ln.code == code), None)
    if existing:
        existing.qty += req.qty
    else:
        sales = _sales_by_code().get(code, {})
        session.add(ZayavkaLine(
            zayavka_id=z.id, code=code, supplier_code=info.get("ref", ""),
            name=req.name.strip() or sales.get("name") or info.get("name", ""),
            qty=req.qty, delivery_price=float(info.get("dprice") or 0.0)))
    await session.commit()
    await session.refresh(z)
    await log_action(session, actor, "zayavka.add", target=supplier, detail=f"code={code}")
    return await _detail(session, z)


class QtyPatch(BaseModel):
    qty: float | None = None       # absolute set
    delta: float | None = None     # OR a relative step (+1 / -1) applied to the stored qty


@router.patch("/lines/{line_id}")
async def edit_line(line_id: int, req: QtyPatch, actor: User = Depends(current_active_user),
                    session: AsyncSession = Depends(get_async_session)):
    ln = await session.get(ZayavkaLine, line_id)
    if not ln:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    zid = ln.zayavka_id
    # a relative delta is applied to the CURRENT stored qty, so rapid +/- clicks can't lose an
    # increment (the stepper sends ±1, not an absolute value read from possibly-stale UI state).
    new_qty = ln.qty + req.delta if req.delta is not None else (req.qty if req.qty is not None else ln.qty)
    if new_qty <= 0:
        await session.delete(ln)          # to zero removes the line
    else:
        ln.qty = new_qty
    await session.commit()
    z = await session.get(Zayavka, zid)
    return await _detail(session, z)


@router.delete("/lines/{line_id}")
async def remove_line(line_id: int, actor: User = Depends(current_active_user),
                      session: AsyncSession = Depends(get_async_session)):
    ln = await session.get(ZayavkaLine, line_id)
    if not ln:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Редът не е намерен.")
    zid = ln.zayavka_id
    await session.delete(ln)
    await session.commit()
    z = await session.get(Zayavka, zid)
    return await _detail(session, z)


# ------------------------------------------------------------------ zayavka detail / meta / list ---
@router.get("/zayavka/{zid}")
async def get_zayavka(zid: int, user: User = Depends(current_active_user),
                      session: AsyncSession = Depends(get_async_session)):
    z = await session.get(Zayavka, zid)
    if not z:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявката не е намерена.")
    return await _detail(session, z)


class MetaPatch(BaseModel):
    notes: str | None = None
    supplier_email: str | None = None
    status: str | None = None


@router.patch("/zayavka/{zid}")
async def patch_zayavka(zid: int, req: MetaPatch, actor: User = Depends(current_active_user),
                        session: AsyncSession = Depends(get_async_session)):
    z = await session.get(Zayavka, zid)
    if not z:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявката не е намерена.")
    if req.notes is not None:
        z.notes = req.notes
    if req.supplier_email is not None:
        z.supplier_email = req.supplier_email.strip()
    if req.status in ("draft", "sent", "received", "cancelled"):
        z.status = req.status
    await session.commit()
    return await _detail(session, z)


# ------------------------------------------------------------------ export (CSV / XLSX) ---
_EXPORT_HEADERS = ["Код (наш)", "Код (доставчик)", "Артикул", "Количество", "Ед. цена (€)", "Сума (€)"]


def _safe_cell(v):
    """Neutralise spreadsheet formula injection: a text cell starting with = + - @ (or a control
    char) is prefixed with an apostrophe so Excel/LibreOffice treat it as text, not a formula. The
    заявка file is emailed to external suppliers, so this matters even for internal data."""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _export_rows(z: Zayavka, lines: list[ZayavkaLine]) -> list[list]:
    return [[_safe_cell(ln.code), _safe_cell(ln.supplier_code), _safe_cell(ln.name),
             ln.qty, round(ln.delivery_price, 2), round(ln.qty * ln.delivery_price, 2)] for ln in lines]


def _build_csv(z: Zayavka, lines: list[ZayavkaLine]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Заявка към {z.supplier}"])
    if z.notes:
        w.writerow([z.notes])
    w.writerow(_EXPORT_HEADERS)
    for r in _export_rows(z, lines):
        w.writerow(r)
    total = round(sum(ln.qty * ln.delivery_price for ln in lines), 2)
    w.writerow(["", "", "", "", "Общо:", total])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM so Excel opens Cyrillic correctly


def _build_xlsx(z: Zayavka, lines: list[ZayavkaLine]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Заявка"
    ws.append([f"Заявка към {z.supplier}"])
    if z.notes:
        ws.append([z.notes])
    ws.append(_EXPORT_HEADERS)
    for r in _export_rows(z, lines):
        ws.append(r)
    ws.append(["", "", "", "", "Общо:", round(sum(ln.qty * ln.delivery_price for ln in lines), 2)])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


_EXPORTERS = {
    "csv": (_build_csv, "text/csv", "csv"),
    "xlsx": (_build_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}


@router.get("/zayavka/{zid}/export")
async def export_zayavka(zid: int, fmt: str = Query("xlsx"),
                         user: User = Depends(current_active_user),
                         session: AsyncSession = Depends(get_async_session)):
    z = await session.get(Zayavka, zid)
    if not z:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявката не е намерена.")
    if fmt not in _EXPORTERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Форматът трябва да е csv или xlsx.")
    build, ctype, ext = _EXPORTERS[fmt]
    data = build(z, await _lines(session, zid))
    fname = f"zayavka-{zid}.{ext}"   # ASCII only — HTTP headers can't carry Cyrillic
    await log_action(session, user, "zayavki.export", target=f"zayavka-{zid}", detail=fmt)  # data leaving → audit
    return StreamingResponse(io.BytesIO(data), media_type=ctype,
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------------------------ send by email (Gmail SMTP) ---
class SendRequest(BaseModel):
    to: str = ""              # supplier email (defaults to the saved one)
    subject: str = ""
    body: str = ""
    confirm: bool = False


@router.post("/zayavka/{zid}/send-email")
async def send_email(zid: int, req: SendRequest, actor: User = Depends(current_active_user),
                     session: AsyncSession = Depends(get_async_session)):
    """Email the заявка (with the Excel attached) to the supplier via Gmail SMTP. Sending is a real
    outbound action → needs confirm=true, and refuses if Gmail isn't configured."""
    from ..config import get_settings
    from ..mailer import mail_configured, send_mail
    z = await session.get(Zayavka, zid)
    if not z:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявката не е намерена.")
    to = (req.to or z.supplier_email).strip()
    if not to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма имейл на доставчика.")
    if not mail_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Изпращането по имейл не е настроено (липсва Gmail App Password). "
                            "Използвай експорт засега.")
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Изпращането е реално действие — потвърди с confirm=true.")
    lines = await _lines(session, zid)
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заявката е празна.")
    subject = req.subject.strip() or f"Заявка от Дарлинг 7 — {z.supplier}"
    total = round(sum(ln.qty * ln.delivery_price for ln in lines), 2)
    body = req.body.strip() or (
        f"Здравейте,\n\nМоля, потвърдете наличност и срок за следната заявка ({len(lines)} артикула, "
        f"общо {total:.2f} €). Подробностите са в прикачения файл.\n\n"
        + (z.notes + "\n\n" if z.notes else "") + "Поздрави,\nДарлинг 7 ООД")
    xlsx = _build_xlsx(z, lines)
    try:
        await send_mail(to=to, subject=subject, body=body,
                        attachment=(f"zayavka-{zid}.xlsx", xlsx,
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                        reply_to=get_settings().gmail_user)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Изпращането се провали: {str(e)[:160]}")
    import datetime as _dt
    z.status = "sent"
    z.sent_at = _dt.datetime.now(_dt.UTC)
    z.sent_via = "email"
    if req.to and not z.supplier_email:
        z.supplier_email = to
    await session.commit()
    await log_action(session, actor, "zayavka.send_email", target=z.supplier, detail=f"to={to} items={len(lines)}")
    return {"ok": True, "sent_to": to, "items": len(lines), "total": total}


@router.get("")
async def list_zayavki(user: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    rows = (await session.execute(
        select(Zayavka).order_by(Zayavka.created_at.desc()).limit(200))).scalars().all()
    # aggregate item count + total in ONE grouped query (not per-zayavka — see list_deliveries)
    agg = {zid: (int(n or 0), round(float(tot or 0), 2)) for zid, n, tot in (await session.execute(
        select(ZayavkaLine.zayavka_id, func.count(),
               func.sum(ZayavkaLine.qty * ZayavkaLine.delivery_price))
        .group_by(ZayavkaLine.zayavka_id))).all()}
    out = []
    for z in rows:
        items, total = agg.get(z.id, (0, 0.0))
        out.append({"id": z.id, "supplier": z.supplier, "status": z.status, "items": items,
                    "total": total, "created_at": z.created_at.isoformat() if z.created_at else None,
                    "sent_at": z.sent_at.isoformat() if z.sent_at else None})
    return {"zayavki": out}
