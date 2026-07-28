# -*- coding: utf-8 -*-
"""Order fulfilment — the Поръчки module.
Reads Shopify orders, tracks OUR operational status per order (fulfillment_state), and drives
one-click Speedy/Econt labels with наложен платеж (COD). The store isn't live yet (0 orders),
so the queue shows an empty state; courier readiness + office lookup + quoting work today.

Label creation is a real-world action: it's owner/staff-gated, requires an explicit confirm
flag, is written to the audit log, and only mints a REAL waybill when COURIER_LIVE is on
(off => a dry-run plan; and a real label additionally requires BARSY_WRITE_LIVE, so cash is
never collected for an unbooked sale)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import barsy, connector, db, shopify_orders
from ..core import courier as courier_mod
from ..db import get_async_session
from ..ids import numeric_oid as _numeric_oid
from ..models import User
from ..upstream import upstream
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/orders", tags=["orders"])


# --------------------------------------------------------------- courier readiness ---
# Barsy speditor name → our courier key. Barsy is the RECORD (its documents/reports carry the courier),
# the panel is the OPERATIONAL side (only we can price + print labels). Barsy exposes no Speditors_create,
# so this is read + reconcile + WARN: an unmapped courier ships fine, it just can't carry a speditor_id
# on the Barsy sale, and the owner has to register it in Barsy's admin UI to close the gap.
_SPEDITOR_ALIASES = {
    "speedy": "speedy", "спиди": "speedy",
    "econt": "econt", "еконт": "econt",
    "boxnow": "boxnow", "box now": "boxnow", "sameday": "sameday",
    "evropat": "evropat", "европът": "evropat", "pigeon": "pigeon",
}


def _barsy_speditors() -> dict[str, dict]:
    """{our courier key: {"speditor_id", "barsy_name"}} from Barsy's read-only speditor list.
    Never raises — a Barsy hiccup must not break the courier-readiness screen."""
    try:
        rows = barsy.call("Speditors_getlist", {"filters": {}}) or []
    except Exception:
        return {}
    if isinstance(rows, dict):                       # some Barsy lists come wrapped
        rows = rows.get("speditors") or rows.get("data") or []
    out: dict[str, dict] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("speditor_name") or r.get("name") or "").strip()
        sid = r.get("speditor_id") or r.get("id")
        key = _SPEDITOR_ALIASES.get(name.lower())
        if key and sid is not None:
            out[key] = {"speditor_id": int(sid), "barsy_name": name}
    return out


@router.get("/couriers")
def courier_readiness(user: User = Depends(current_active_user)):
    """Live status of each courier: configured + (for configured ones) a sender-identity
    ping, so staff see at a glance who's ready to print labels. Also reconciles each courier against
    Barsy's speditor list so drift ("we ship with it, Barsy doesn't know it") is visible, not silent."""
    speditors = _barsy_speditors()
    out = []
    for key, name, configured in courier_mod.available():
        sp = speditors.get(key)
        row = {"key": key, "name": name, "configured": bool(configured),
               "ready": False, "sender": "", "note": "",
               # Barsy-record reconciliation (None = this courier has no Barsy speditor)
               "barsy_speditor_id": (sp or {}).get("speditor_id"),
               "barsy_name": (sp or {}).get("barsy_name", ""),
               "barsy_synced": sp is not None}
        if configured:
            try:
                p = courier_mod.get(key).ping()
                row["sender"] = p.get("sender_name", "")
                # Econt with no sender profile can't create labels yet
                if key == "econt" and p.get("sender_configured") is False:
                    row["ready"] = False
                    row["note"] = "Нужен е адрес на подателя (в Еконт профила или ECONT_SENDER_*)."
                else:
                    row["ready"] = True
                    if p.get("sender_place"):
                        row["note"] = "Подател: " + p["sender_place"]
            except Exception as e:
                row["note"] = f"Грешка при връзка: {str(e)[:100]}"
        else:
            row["note"] = "Няма ключове в ~/.hermes/.env"
        # drift warning: we can ship with it, but Barsy documents won't carry the courier
        if configured and not row["barsy_synced"]:
            row["note"] = (row["note"] + " · Липсва в списъка със спедитори на Barsy — "
                           "пратките се издават нормално, но в Barsy не се записва куриер. "
                           "Добави го в Barsy (Настройки → Спедитори).").strip(" ·")
        out.append(row)
    unmapped = [r["key"] for r in out if r["configured"] and not r["barsy_synced"]]
    return {"couriers": out, "barsy_speditors_found": len(speditors), "unmapped": unmapped}


@router.get("/couriers/{key}/offices")
def courier_offices(key: str, city: str = Query("", description="филтър по град"),
                    user: User = Depends(current_active_user)):
    """Office / locker picker for door-to-office delivery."""
    with upstream(key):
        offices = courier_mod.get(key).offices(city=city or None)
    return {"courier": key, "count": len(offices), "offices": offices[:200]}


# --------------------------------------------------------------------- order queue ---
@router.get("")
def list_orders(
    user: User = Depends(current_active_user),
    unfulfilled_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=250),
):
    """Recent Shopify orders merged with our operational status. Empty until the store
    launches — returns [] gracefully if Shopify has no orders (or is unreachable)."""
    try:
        orders = shopify_orders.fetch_orders(limit=limit, unfulfilled_only=unfulfilled_only)
    except Exception as e:
        return {"orders": [], "statuses": shopify_orders.STATUS_BG,
                "warning": f"Shopify недостъпен: {str(e)[:120]}"}
    smap = shopify_orders.state_map()
    for o in orders:
        st = smap.get(o["id"], {})
        o["op_status"] = st.get("status") or "new"
        o["op_status_bg"] = shopify_orders.STATUS_BG.get(o["op_status"], o["op_status"])
        o["courier"] = st.get("courier")
        o["tracking"] = st.get("tracking")
        o["label_ref"] = st.get("label_ref")
    return {"orders": orders, "statuses": shopify_orders.STATUS_BG}


# --------------------------------------------------------------- quote / label / track ---
class ShipRequest(BaseModel):
    courier: str = Field(max_length=32)
    office_id: str | None = Field(None, max_length=64)  # deliver to a courier office/locker, else door
    weight_kg: float = Field(1.0, gt=0, le=1000)
    # Дарлинг DEFAULTS (home goods): fragile on, преглед on — but the packer can override per order
    fragile: bool = True
    inspection: bool = True             # преглед (open before pay), not тест
    confirm: bool = False              # required True to actually create a label


def _order_or_404(order_id: str):
    for o in shopify_orders.fetch_orders(limit=250):
        if order_id in (o["id"], o["name"], _numeric_oid(o["id"])):
            return o
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Поръчката не е намерена.")


def _is_already_finalised_error(msg: str) -> bool:
    """BACKSTOP for an idempotent re-click. The connector's finalise_order short-circuits when its OWN
    in-process state remembers the sale (kind=="sale") — but that state does NOT survive a container
    restart, so after a redeploy a second Финализирай re-calls Accounts_createfromclientorder and Barsy
    itself refuses it (HTTP 501 'по клиентска заявка N вече има създадена/и сметка/и …'). No double
    decrement happens — Barsy is the backstop — but the operator would otherwise see a scary 502 for a
    benign re-click. We recognise that specific Barsy response and surface it as a calm already_finalised.
    (The durable fix lives in the connector: treat this Barsy 501 as idempotent + repair its state.)"""
    m = msg.lower()
    return "createfromclientorder" in m and ("вече има" in m or "already" in m)


def _order_finalised(numeric_oid: str) -> bool:
    """True iff Barsy already holds the SALE for this order (finalised). Uses the connector's
    finalise_order DRY-RUN (do_write=False), which reports `already_finalised` when the clientorder
    already has a Barsy account — the durable fix reconciles that against Barsy, so it survives a
    container restart. FAIL-CLOSED: anything other than a clear already_finalised is treated as NOT
    finalised, so we never ship/label an un-decremented order (a false 'not finalised' just makes staff
    re-click Финализирай, which is idempotent). Guards the ship-before-finalise oversell window — see
    RECONCILIATION_SPEC / inventory-reconciliation-rule: shipping clears Shopify `committed` while Barsy
    stock is still full, and the on_hand mirror would then re-add the sold units."""
    try:
        r = connector.finalise_order(numeric_oid, False)   # dry-run, no write
    except AttributeError:
        return True    # finalise not wired at all → don't block the existing label flow
    except Exception:
        return False
    if not isinstance(r, dict):
        return False
    return r.get("status") == "duplicate" or r.get("reason") == "already_finalised"


def _finalise_for_pack(o: dict) -> dict:
    """Book the sale as part of the Етикет/pack action (owner's flow: label = finalise + бон + sent).
    Decrement stock + fiscal бон (print at packing) + mark paid via the connector, BEFORE the courier
    label. Idempotent (already_finalised on a re-label). Raises HTTPException if the order can't be booked
    so we never create a real courier label for an un-booked order. `do_write` is the master Barsy gate."""
    do_write = get_settings().barsy_write_live
    try:
        fin = connector.finalise_order(_numeric_oid(o["id"]), do_write)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Осчетоводяването не е налично (connector.finalise_order липсва).")
    except Exception as e:
        if _is_already_finalised_error(str(e)):
            return {"status": "already_finalised"}
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Осчетоводяването на {o['name']} преди етикет не успя: {str(e)[:150]}")
    # "already_finalised" arrives as EITHER status (our own 501-backstop above) or reason (the
    # connector's own shape: {"status":"duplicate","reason":"already_finalised"}). Accept both.
    ok = isinstance(fin, dict) and (
        fin.get("status") in ("ok", "duplicate", "already_finalised")
        or fin.get("reason") == "already_finalised")
    if not ok:
        reason = (fin or {}).get("reason", "?") if isinstance(fin, dict) else "?"
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Не мога да осчетоводя {o['name']} преди етикет ({reason}). Провери поръчката.")
    return fin if isinstance(fin, dict) else {"status": "ok"}


def _courier_or_502(key: str):
    """Resolve the adapter, mapping an unknown/misconfigured courier to the same 502 the live label
    path returns (the dry-run branches skip create_shipment, so they'd otherwise 'succeed' for a
    courier that doesn't exist)."""
    try:
        return courier_mod.get(key)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])


def _to_kg(v) -> float:
    try:
        return float(str(v).replace(",", ".")) if v else 0.0
    except (TypeError, ValueError):
        return 0.0


def _barsy_weight(code) -> float:
    """Weight from BARSY (the system of record): `extra_data.weight_kg` (a string). NOTE: extra_data
    is only returned when explicitly requested via extra_properties. Barsy is low-tier + throttles,
    so retry once; any failure → 0.0 so the caller falls back to the pipeline record."""
    for _ in range(2):
        try:
            rows = barsy.call("Articles_getlist", {
                "filters": {"code": str(code)}, "extra_properties": {"extra_data": "1"}, "length": 1})
            ed = (rows[0].get("extra_data") if rows else None) or {}
            return _to_kg(ed.get("weight_kg")) if isinstance(ed, dict) else 0.0
        except Exception:
            continue
    return 0.0


def _pipeline_weight(code) -> float:
    """Fallback weight from the pipeline record's `Тегло (кг)` — has data now, before the full
    catalog (with extra_data.weight_kg) is pushed to Barsy."""
    con = db.connect()
    try:
        row = con.execute(
            "SELECT json_extract(record_json,'$.fields.\"Тегло (кг)\"') FROM products WHERE code=?",
            (str(code),)).fetchone()
    except Exception:
        return 0.0
    finally:
        con.close()
    return _to_kg(row[0] if row else None)


def _product_weight(code) -> float:
    """Per-product weight (кг). Barsy is the source of truth (extra_data.weight_kg); the pipeline
    `Тегло (кг)` is the fallback until the full catalog carries weight in Barsy."""
    if not code:
        return 0.0
    return _barsy_weight(code) or _pipeline_weight(code)


def _order_weight(o: dict) -> float:
    """Total shipment weight = Σ product weight × qty over the order's line items (SKU = our code)."""
    total = sum(_product_weight(it.get("sku")) * (it.get("qty") or 1) for it in (o.get("items") or []))
    return round(total, 3)


def _shipment_from_order(o: dict, req: "ShipRequest") -> "courier_mod.Shipment":
    weight = _order_weight(o) or req.weight_kg  # real summed weight; fall back to the form's value
    return courier_mod.Shipment(
        order_ref=o["name"], recipient=o["customer"], phone=o["phone"],
        office_id=req.office_id,
        address=None if req.office_id else {
            "city": o.get("city"), "postcode": o.get("zip"),
            "street": o.get("address"), "street_no": ""},
        cod_amount=o.get("cod_amount", 0.0), weight_kg=weight,
        declared_value=o.get("total"),   # обявена стойност = стойността на поръчката
        fragile=req.fragile, inspection=req.inspection,  # Дарлинг defaults, packer-overridable
        contents="стоки за дома",
    )


@router.post("/{order_id}/quote")
def quote(order_id: str, req: ShipRequest, user: User = Depends(current_active_user)):
    """Price a shipment — no side effects (safe to call freely)."""
    o = _order_or_404(order_id)
    with upstream(req.courier):
        return courier_mod.get(req.courier).calculate(_shipment_from_order(o, req))


@router.post("/{order_id}/label")
async def make_label(
    order_id: str, req: ShipRequest,
    actor: User = Depends(require_roles("staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a REAL shipment + label (наложен платеж if the order is COD). Gated: needs
    confirm=True, an allowed role, COURIER_LIVE on (else a dry-run plan, no label, not shipped),
    audit-logged, and refuses to double-label an order.
    Blocking courier/Shopify/DB calls run in a threadpool so the event loop stays free."""
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Създаването на етикет е реално действие — потвърди с confirm=true.")
    o = await run_in_threadpool(_order_or_404, order_id)
    # double-label guard: never create a second real COD shipment for an order that already has a
    # label / tracking / is past 'new'. `labeling` is a transient CLAIM (below) — a concurrent
    # confirmed request sees it and backs off, shrinking the TOCTOU window to the tiny gap between
    # the two writes instead of the whole (seconds-long) courier call.
    existing = shopify_orders.state_map().get(o["id"], {})
    if existing.get("label_ref") or existing.get("tracking") or \
            existing.get("status") in ("labeling", "shipped", "delivered", "cod_paid"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Поръчка {o['name']} вече има издаден етикет или се обработва "
            f"({existing.get('courier') or ''} {existing.get('tracking') or ''}).")
    # Етикет = pack (owner's flow): the label IS the finalise moment. Book the sale first — decrement
    # stock + fiscal бон (print at packing) + mark paid — THEN create the courier label, THEN mark sent
    # (fulfilment, below). Finalise BEFORE the courier so a courier failure leaves a recoverable booked
    # order (retry just re-labels; finalise is idempotent) rather than a real shipment with no booking.
    fin = await run_in_threadpool(_finalise_for_pack, o)
    courier_live = get_settings().courier_live
    # The two gates must stay COHERENT: a real waybill requires a real booking. _finalise_for_pack accepts
    # the connector's dry-run as success (status ok + dry_run), so with BARSY_WRITE_LIVE off nothing is
    # booked — no продажба, no fiscal бон, no изписване — while the courier would still collect real cash.
    # (already_finalised is exempt: that dry-run reconciled against Barsy, the sale IS there.)
    _already = isinstance(fin, dict) and (
        fin.get("status") in ("duplicate", "already_finalised")
        or fin.get("reason") == "already_finalised")
    if courier_live and isinstance(fin, dict) and fin.get("dry_run") and not _already:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Осчетоводяването на {o['name']} е в пробен режим (dry-run) — включи BARSY_WRITE_LIVE "
            f"преди реален етикет. Реален етикет без осчетоводена продажба означава събрани пари "
            f"без продажба, без бон и без изписване.")
    if not courier_live:
        # COURIER_LIVE off (the pre-launch default): a waybill costs money the moment it's minted and the
        # наложен платеж collects real cash, so we never even reach the adapter (which would raise anyway).
        # Return the plan WITHOUT claiming 'labeling' (nothing to release, and a rehearsal must not reset
        # a 'picking' order), without marking shipped and without a Shopify fulfilment — an order that
        # looks sent with no label is worse than no label at all.
        _courier_or_502(req.courier)   # an unknown courier must fail even in a rehearsal
        shp = await run_in_threadpool(_shipment_from_order, o, req)
        plan = {"dry_run": True, "gated": True, "courier": req.courier, "order_ref": o["name"],
                "tracking_number": None, "label_ref": None,
                "recipient": shp.recipient, "phone": shp.phone, "office_id": shp.office_id,
                "address": shp.address, "cod_amount": shp.cod_amount, "weight_kg": shp.weight_kg,
                "finalise": fin,
                "note": ("ПРОБЕН РЕЖИМ: реален етикет НЕ е създаден (COURIER_LIVE е изключен). "
                         "Поръчката не е маркирана като изпратена и не е подадена към Shopify. "
                         "Включи COURIER_LIVE за истинска пратка.")}
        await log_action(session, actor, "label.dry_run", target=o["name"],
                         detail=f"{req.courier} COD={shp.cod_amount}€ (без реален етикет)")
        return plan
    # double-label guard passed + booked → now claim and create the real label
    await run_in_threadpool(shopify_orders.set_state, o["id"], o["name"], status="labeling")  # claim
    try:
        result = await run_in_threadpool(
            courier_mod.get(req.courier).create_shipment,
            _shipment_from_order(o, req), courier_live)  # do_write = COURIER_LIVE (true here)
    except Exception as e:
        # release the claim so a retry can proceed
        try:
            await run_in_threadpool(shopify_orders.set_state, o["id"], o["name"], status="new")
        except Exception:
            pass
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    # label exists now — persist state even if this write hiccups, so we never lose the
    # fact that a real label was created (which would invite a duplicate).
    try:
        await run_in_threadpool(
            shopify_orders.set_state, o["id"], o["name"],
            status="shipped", courier=req.courier,
            tracking=result.get("tracking_number"), cod_amount=result.get("cod_amount", 0),
            label_ref=str(result.get("label_ref", "")))
    except Exception as e:
        result["warning"] = (f"Етикетът е създаден ({result.get('tracking_number')}), но "
                             f"статусът не се запази: {str(e)[:80]}")
    # #83 / RECONCILIATION_SPEC rule B: dispatch = push a Shopify fulfilment (clears `committed`,
    # drops on_hand once). NON-FATAL — the real label already exists, so a fulfilment hiccup must not
    # fail the label call; it surfaces as a warning (and the connector logs a Проблеми issue).
    try:
        # `speditor` stamps the waybill + courier onto the Barsy sale account (best-effort — a Barsy
        # stamp failure never fails the Shopify fulfilment). That's what makes COD reconciliation able
        # to match Barsy↔courier by waybill directly. Keyword-only: the two connector fns order their
        # params differently. The connector normalises the courier name (speedy/Спиди → 2, econt → 1);
        # an unregistered courier resolves to None and the waybill is still stamped.
        fulfil = await run_in_threadpool(
            connector.ship_order, _numeric_oid(o["id"]),
            result.get("tracking_number"), get_settings().barsy_write_live,
            speditor=req.courier)
        result["fulfillment"] = fulfil
        result["finalise"] = fin
        if isinstance(fulfil, dict) and fulfil.get("status") not in ("ok", "duplicate", None):
            result["warning"] = (result.get("warning", "") +
                                 f" Пратката не се отбеляза в Shopify ({fulfil.get('reason', '?')}).").strip()
        # the Barsy waybill stamp is a soft extra — surface a miss as a warning, never an error
        bt = (fulfil or {}).get("barsy_tracking") if isinstance(fulfil, dict) else None
        if isinstance(bt, dict) and bt.get("written") is False:
            result["warning"] = (result.get("warning", "") +
                                 " Товарителницата не се записа в Barsy (ще се съпостави по поръчка).").strip()
    except Exception as e:
        result["warning"] = (result.get("warning", "") +
                             f" Shopify fulfilment пропусна: {str(e)[:80]}.").strip()
    await log_action(session, actor, "label.create", target=o["name"],
                     detail=f"{req.courier} {result.get('tracking_number')} "
                            f"COD={result.get('cod_amount')}€")
    return result


@router.get("/track/{key}/{tracking}")
def track(key: str, tracking: str, user: User = Depends(current_active_user)):
    with upstream(key):
        return courier_mod.get(key).track(tracking)


class ManualShip(BaseModel):
    courier: str = Field(max_length=32)
    recipient: str = Field(max_length=200)
    phone: str = Field(max_length=32)
    email: str = Field("", max_length=254)
    office_id: str | None = Field(None, max_length=64)
    city: str = Field("", max_length=120)
    postcode: str = Field("", max_length=16)
    street: str = Field("", max_length=200)
    street_no: str = Field("", max_length=32)
    weight_kg: float = Field(1.0, gt=0, le=1000)
    cod_amount: float = Field(0.0, ge=0, le=1_000_000)  # ge=0: a negative COD is silently read as prepaid downstream
    contents: str = Field("стоки за дома", max_length=255)
    fragile: bool = True
    inspection: bool = True
    confirm: bool = False


def _manual_shipment(req: "ManualShip"):
    return courier_mod.Shipment(
        order_ref="ръчна", recipient=req.recipient, phone=req.phone, email=req.email,
        office_id=req.office_id,
        address=None if req.office_id else {"city": req.city, "postcode": req.postcode,
                                            "street": req.street, "street_no": req.street_no},
        cod_amount=req.cod_amount, weight_kg=req.weight_kg, contents=req.contents,
        fragile=req.fragile, inspection=req.inspection)


@router.post("/manual-quote")
def manual_quote(req: ManualShip, user: User = Depends(current_active_user)):
    """Price an ad-hoc shipment (no order needed) — side-effect-free."""
    with upstream(req.courier):
        return courier_mod.get(req.courier).calculate(_manual_shipment(req))


@router.post("/manual-label")
async def manual_label(
    req: ManualShip,
    actor: User = Depends(require_roles("staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a real ad-hoc label (Speedy/Econt) with optional COD. Gated + audited. COURIER_LIVE off
    => dry-run plan only: no waybill, no COD collection (this endpoint has no order behind it, so an
    ungated call would mint a real label for a free-text recipient and amount)."""
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Създаването на етикет е реално действие — потвърди с confirm=true.")
    if not req.recipient.strip() or not req.phone.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужни са получател и телефон.")
    courier_live = get_settings().courier_live
    if not courier_live:
        _courier_or_502(req.courier)   # an unknown courier must fail even in a rehearsal
        shp = _manual_shipment(req)
        plan = {"dry_run": True, "gated": True, "courier": req.courier,
                "tracking_number": None, "label_ref": None,
                "recipient": shp.recipient, "phone": shp.phone, "office_id": shp.office_id,
                "address": shp.address, "cod_amount": shp.cod_amount, "weight_kg": shp.weight_kg,
                "note": ("ПРОБЕН РЕЖИМ: реален етикет НЕ е създаден (COURIER_LIVE е изключен). "
                         "Включи COURIER_LIVE за истинска пратка.")}
        await log_action(session, actor, "label.manual.dry_run", target=req.recipient,
                         detail=f"{req.courier} COD={req.cod_amount}€ (без реален етикет)")
        return plan
    try:
        result = await run_in_threadpool(
            courier_mod.get(req.courier).create_shipment, _manual_shipment(req), courier_live)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    await log_action(session, actor, "label.manual", target=req.recipient,
                     detail=f"{req.courier} {result.get('tracking_number')} COD={result.get('cod_amount')}€")
    return result


class StatusUpdate(BaseModel):
    order_id: str
    order_name: str
    status: str


@router.post("/status")
async def set_status(
    body: StatusUpdate,
    actor: User = Depends(require_roles("staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    if body.status not in shopify_orders.STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Непознат статус.")
    shopify_orders.set_state(body.order_id, body.order_name, status=body.status)
    await log_action(session, actor, "order.status", target=body.order_name, detail=body.status)
    return {"ok": True, "status": body.status}


# --------------------------------------------------- review → finalise / refuse (Barsy) ---
# The owner's clientorder model: an online order books as a Barsy CLIENTORDER (no decrement) awaiting
# staff review. Staff then FINALISE it (→ closed PAID sale: decrement + a REAL fiscal бон) or REFUSE it
# (→ void the clientorder + cancel the Shopify order; nothing decremented — the nekorekten-fake-customer
# path). The connector owns the Barsy/Shopify write shapes (finalise_order / refuse_order); this router
# owns the gate (BARSY_WRITE_LIVE → dry-run when off), the confirm flag, the role check, and the audit line.
class OrderAction(BaseModel):
    confirm: bool = False


@router.post("/{order_id}/finalise")
async def finalise(
    order_id: str, body: OrderAction,
    actor: User = Depends(require_roles("owner", "manager", "staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """MANUAL RECOVERY ONLY — the UI never calls this; Етикет (POST /{id}/label) is the sanctioned
    finalise path, because finalising and labelling are one packing action.

    Books the order's Barsy clientorder as a CLOSED, **PAID** SALE (Accounts_createfromclientorder,
    flag_close_account:1, **print_receipt:1**, payment under "Наложен платеж" method 6) — i.e. it
    DECREMENTS stock AND **PRINTS A REAL FISCAL БОН** on the device (paid-at-packing model; the older
    "fiscal:0, no бон" behaviour is gone).

    ⚠️ It fiscalises WITHOUT creating a courier label: the customer gets a бон for goods that have no
    waybill, so use it only to repair a half-finished pack (e.g. the label call died after the courier
    call) — then create/attach the label separately. For the normal flow use Етикет.
    Gated (BARSY_WRITE_LIVE → dry-run when off) + confirm + role + audit."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Финализирането е реален запис в Barsy — потвърди с confirm=true.")
    o = await run_in_threadpool(_order_or_404, order_id)
    do_write = get_settings().barsy_write_live
    try:
        result = await run_in_threadpool(connector.finalise_order, _numeric_oid(o["id"]), do_write)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Финализирането не е налично (connector.finalise_order липсва).")
    except Exception as e:
        if _is_already_finalised_error(str(e)):
            # benign re-click after a connector restart — Barsy already booked the sale, nothing decremented twice
            result = {"status": "already_finalised", "order_ref": o["name"], "detail": "Barsy: вече осчетоводена."}
            await log_action(session, actor, "order.finalise", target=o["name"], detail=str(result)[:300])
            return result
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    await log_action(session, actor, "order.finalise", target=o["name"], detail=str(result)[:300])
    return result


@router.post("/{order_id}/refuse")
async def refuse(
    order_id: str, body: OrderAction,
    actor: User = Depends(require_roles("owner", "manager", "staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Refuse an online order (e.g. a nekorekten fake customer): cancel the Shopify order (releases the
    reservation) + void/storno the Barsy side via the connector. Clientorder-stage → VOID (nothing
    decremented); finalised sale → STORNO (needs the fiscal УНП). Gated + confirm + role + audit."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Отказът е реално действие — потвърди с confirm=true.")
    o = await run_in_threadpool(_order_or_404, order_id)
    do_write = get_settings().barsy_write_live
    try:
        result = await run_in_threadpool(connector.refuse_order, _numeric_oid(o["id"]), do_write)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Отказът не е наличен (connector.refuse_order липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    await log_action(session, actor, "order.refuse", target=o["name"], detail=str(result)[:300])
    return result


class ShipAction(BaseModel):
    tracking: str = Field("", max_length=64)
    confirm: bool = False


@router.post("/{order_id}/ship")
async def ship(
    order_id: str, body: ShipAction,
    actor: User = Depends(require_roles("owner", "manager", "staff", "packer")),
    session: AsyncSession = Depends(get_async_session),
):
    """Dispatch (#83 / RECONCILIATION_SPEC rule B): push a Shopify fulfilment for the order (clears
    `committed`, drops on_hand once) via the connector. Normally fired automatically on label success;
    exposed explicitly for re-runs and for orders shipped without a panel-created label. The connector
    dedups (a second call on an already-fulfilled order returns a skip). Gated + confirm + role + audit."""
    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Изпращането отбелязва пратката в Shopify — потвърди с confirm=true.")
    o = await run_in_threadpool(_order_or_404, order_id)
    # finalise-before-ship guard (same invariant as make_label): never fulfil an un-decremented order.
    if not await run_in_threadpool(_order_finalised, _numeric_oid(o["id"])):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Финализирай {o['name']} преди изпращане — изписването още не е направено.")
    do_write = get_settings().barsy_write_live
    try:
        result = await run_in_threadpool(
            connector.ship_order, _numeric_oid(o["id"]), body.tracking or None, do_write,
            # the courier isn't in the request body here — take it from the order's own recorded state
            # (set when the label was made) so the Barsy waybill stamp carries the right speditor.
            speditor=(shopify_orders.state_map().get(o["id"], {}).get("courier") or None))
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Изпращането не е налично (connector.ship_order липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    await log_action(session, actor, "order.ship", target=o["name"], detail=str(result)[:300])
    return result
