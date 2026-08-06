# -*- coding: utf-8 -*-
"""Delivery fees (DELIVERY_FEE_DESIGN v2, owner-approved 2026-08-05) — courier-specific pricing.

Checkout shows EVERY enabled courier×mode at price = the courier's REAL tariff + a small
owner-set buffer covering refused-COD round trips (+ the courier's COD fee folded into the
shown price when наложен платеж is the payment method — no separate label, per Kris).
Tariffs are DISTANCE-FLAT nationally (verified live 2026-08-05: София = Варна = Ихтиман per
courier), so the "exact API amount" is a small tariff TABLE refreshed from the quote APIs —
no live courier call in the checkout path. Free shipping ≥ 77 EUR; cheapest sorts first.

Refresh model: staleness-triggered (a public read older than 24h schedules a background
refresh) + manual POST /refresh. A failed courier refresh KEEPS yesterday's numbers and
records a panel issue — watch staleness, not errors (a dead refresh must not look like a
quiet day, and checkout must never lose its prices because a quote API hiccuped)."""
from __future__ import annotations

import datetime as _dt
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..config import get_settings
from ..core import courier as courier_mod
from ..db import async_session_maker, get_async_session
from ..issues import record_issue
from ..models import AppSetting, ShippingRate, User
from ..users import require_roles

router = APIRouter(tags=["shipping-rates"])
log = logging.getLogger(__name__)

BRACKETS = [1.0, 3.0, 5.0, 10.0]          # kg — from the live matrix
FREE_OVER_EUR = 77.0                       # owner decision 2026-08-05 — the DEFAULT; the live
FREE_OVER_KEY = "shipping.free_over_eur"   # value is owner-editable and lives in app_setting
COD_MEASURE_EUR = 50.0                     # COD fee measured at this collection amount
STALE_AFTER_H = 24.0

# per-courier spec: which modes we price + the owner-approved default buffers (EUR). Buffers are
# applied ONLY when a row is first created — after that they are owner property (PATCH below).
_SPEC: dict = {
    "speedy": {"modes": ("office", "door"), "buffer": {"office": 0.30, "door": 0.50}, "enabled": True},
    "econt":  {"modes": ("office", "door"), "buffer": {"office": 0.50, "door": 0.50}, "enabled": True},
    # Pigeon: adapter+keys live, but no Svishtov office to ship FROM yet — priced when refresh
    # succeeds, offered only when the owner flips enabled (mirrors public_couriers staying dark).
    "pigeon": {"modes": ("office",), "buffer": {"office": 0.25}, "enabled": False},
    # BoxNow (2026-08-05): LOCKER-ONLY, contract-flat 1.54 EUR incl VAT, COD fee 0 (owner-
    # confirmed; COD is card-by-link, never cash). Buffer 0.10 — refusal risk is lower than
    # door COD but uncollected-in-48h returns still exist. DARK until the BOX NOW-required
    # test order is confirmed AND the storefront locker picker ships (woo lane) — mode
    # "locker" was announced to the woo agent before this could ever serve.
    "boxnow": {"modes": ("locker",), "buffer": {"locker": 0.10}, "enabled": False},
}
_MODE_BG = {"office": "до офис", "door": "до адрес", "locker": "до автомат"}


async def get_free_over(session: AsyncSession) -> tuple[float, str]:
    """(threshold, source) where source is "stored" or "default".

    Two states reported, not one number. A page that shows «77 €» cannot tell the owner
    whether that is their setting or the fallback, and those differ the moment somebody
    wonders why an edit did not take. An unparseable stored value falls back to the default
    and says so — never to 0, which would make every order ship free.
    """
    row = await session.get(AppSetting, FREE_OVER_KEY)
    raw = (row.value if row else "").strip()
    if not raw:
        return FREE_OVER_EUR, "default"
    try:
        value = float(raw)
    except ValueError:
        log.warning("shipping: free_over_eur is unreadable (%r) — using the default %s",
                    raw, FREE_OVER_EUR)
        return FREE_OVER_EUR, "default"
    if not (0 < value <= 10_000):
        log.warning("shipping: free_over_eur %s is out of range — using the default %s",
                    value, FREE_OVER_EUR)
        return FREE_OVER_EUR, "default"
    return round(value, 2), "stored"


def _stale_hours(rows) -> float | None:
    """Age of the freshest tariff in hours, or None when nothing has ever been refreshed."""
    newest = max((r.refreshed_at for r in rows if r.refreshed_at), default=None)
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=_dt.UTC)     # SQLite drops tz on the way back
    return round((_dt.datetime.now(_dt.UTC) - newest).total_seconds() / 3600.0, 1)


def _quote(adapter, mode: str, weight: float, cod: float, office_id, address) -> float:
    shp = courier_mod.Shipment(order_ref="RATES-REFRESH", recipient="X", phone="0888000000",
                               weight_kg=float(weight), cod_amount=cod,
                               office_id=office_id if mode == "office" else None,
                               address=None if mode == "office" else address)
    return float(adapter.calculate(shp)["price"])


def _refresh_courier(name: str) -> list[dict]:
    """All bracket rows for one courier, from live quotes. Raises on any failure — the caller
    keeps the old rows and records the issue (fail open on advice, never lose prices)."""
    adapter = courier_mod.get(name)
    if not adapter.configured():
        raise courier_mod.CourierError(f"{name}: не е конфигуриран")
    offs = adapter.offices()
    if not offs:
        raise courier_mod.CourierError(f"{name}: няма офиси")
    office_id = offs[0]["id"]
    address = {"city": "София", "postcode": "1000"}
    spec = _SPEC[name]
    rows: list[dict] = []
    # COD fee once per courier (1 kg, office) — the surcharge, not who pays the courier fee
    base_1kg = _quote(adapter, "office", 1.0, 0.0, office_id, address)
    cod_fee = round(_quote(adapter, "office", 1.0, COD_MEASURE_EUR, office_id, address) - base_1kg, 2)
    for mode in spec["modes"]:
        for w in BRACKETS:
            cost = base_1kg if (mode == "office" and w == 1.0) else _quote(
                adapter, mode, w, 0.0, office_id, address)
            rows.append({"courier": name, "mode": mode, "max_kg": w,
                         "label_cost": round(cost, 2), "cod_fee": max(cod_fee, 0.0)})
    return rows


async def refresh_rates() -> dict:
    """Refresh every courier in _SPEC. Per-courier isolation: one failing quote API keeps its
    OLD rows (+ a panel issue) and never blocks the others."""
    out: dict = {}
    now = _dt.datetime.now(_dt.UTC)
    for name in _SPEC:
        try:
            rows = await run_in_threadpool(_refresh_courier, name)
        except Exception as e:  # noqa: BLE001 — any quote-API failure means "keep old prices"
            out[name] = f"error: {str(e)[:120]}"
            await record_issue(source="panel", topic="shipping-rates", order_ref=name,
                               severity="warning", kind="rates_refresh_failed",
                               summary=f"Тарифите на {name} не се опресниха — ползват се старите.",
                               detail={"error": str(e)[:300]})
            continue
        async with async_session_maker() as session:
            existing = {(r.courier, r.mode, r.max_kg): r for r in (await session.execute(
                select(ShippingRate).where(ShippingRate.courier == name))).scalars().all()}
            for row in rows:
                key = (row["courier"], row["mode"], row["max_kg"])
                if key in existing:
                    existing[key].label_cost = row["label_cost"]
                    existing[key].cod_fee = row["cod_fee"]
                    existing[key].refreshed_at = now
                else:
                    spec = _SPEC[name]
                    session.add(ShippingRate(
                        courier=row["courier"], mode=row["mode"], max_kg=row["max_kg"],
                        label_cost=row["label_cost"], cod_fee=row["cod_fee"],
                        buffer=spec["buffer"][row["mode"]], enabled=spec["enabled"],
                        refreshed_at=now))
            await session.commit()
        out[name] = f"ok: {len(rows)} rows"
    return out


# ------------------------------------------------------------------ manager surface ---
class RatePatch(BaseModel):
    buffer: float | None = None
    enabled: bool | None = None


@router.get("/api/shipping-rates")
async def list_rates(user: User = Depends(require_roles("manager")),
                     session: AsyncSession = Depends(get_async_session)) -> dict:
    """Everything the config page needs in one read: the rows, what the design SUGGESTS for
    each buffer, how old the tariffs are, and whether the free-over threshold is the owner's
    or the fallback."""
    rows = (await session.execute(select(ShippingRate).order_by(
        ShippingRate.courier, ShippingRate.mode, ShippingRate.max_kg))).scalars().all()
    free_over, source = await get_free_over(session)
    stale_h = _stale_hours(rows)
    return {"rates": [{
        "id": r.id, "courier": r.courier, "mode": r.mode, "max_kg": r.max_kg,
        "courier_bg": courier_mod.BG_LABELS.get(r.courier, r.courier),
        "mode_bg": _MODE_BG.get(r.mode, r.mode),
        "label_cost": r.label_cost, "cod_fee": r.cod_fee, "buffer": r.buffer,
        # what DELIVERY_FEE_DESIGN v2 calculated for this courier×mode. Shown beside the set
        # value so a drift from the approved model is visible rather than forgotten.
        "suggested_buffer": (_SPEC.get(r.courier) or {}).get("buffer", {}).get(r.mode),
        "enabled": r.enabled,
        "price_eur": round(r.label_cost + r.buffer, 2) if r.label_cost > 0 else None,
        "price_cod_eur": round(r.label_cost + r.buffer + r.cod_fee, 2) if r.label_cost > 0 else None,
        # a row that was never priced must not read as "0.00 € shipping" on the page either
        "priced": r.label_cost > 0,
        "refreshed_at": r.refreshed_at.isoformat() if r.refreshed_at else None,
    } for r in rows],
        "free_over_eur": free_over, "free_over_source": source,
        "free_over_default": FREE_OVER_EUR,
        "stale_hours": stale_h, "stale_after_h": STALE_AFTER_H,
        "stale": stale_h is None or stale_h > STALE_AFTER_H}


class ConfigPatch(BaseModel):
    free_over_eur: float


@router.patch("/api/shipping-rates/config")
async def patch_config(req: ConfigPatch, actor: User = Depends(require_roles("manager")),
                       session: AsyncSession = Depends(get_async_session)) -> dict:
    """The one global number on the config page. Refused rather than clamped outside a sane
    window: a zero here would make every order ship free, and a typo'd 7 instead of 77 is a
    margin decision nobody made on purpose."""
    value = req.free_over_eur
    if not (0 < value <= 10_000):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Прагът за безплатна доставка трябва да е между 0 и 10 000 €.")
    row = await session.get(AppSetting, FREE_OVER_KEY)
    if row is None:
        row = AppSetting(key=FREE_OVER_KEY)
        session.add(row)
    row.value = f"{round(value, 2)}"
    row.updated_by = actor.email
    row.updated_at = _dt.datetime.now(_dt.UTC)
    await session.commit()
    await log_action(session, actor, "shipping.config", target="free_over_eur",
                     detail=f"{row.value} €")
    return {"ok": True, "free_over_eur": round(value, 2)}


@router.patch("/api/shipping-rates/{rate_id:int}")
async def patch_rate(rate_id: int, req: RatePatch,
                     actor: User = Depends(require_roles("manager")),
                     session: AsyncSession = Depends(get_async_session)) -> dict:
    r = await session.get(ShippingRate, rate_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава тарифа.")
    changed = []
    if req.buffer is not None:
        if req.buffer < 0 or req.buffer > 5:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Буферът е извън разумното (0–5 €).")
        r.buffer = round(req.buffer, 2)
        changed.append(f"buffer={r.buffer}")
    if req.enabled is not None:
        r.enabled = bool(req.enabled)
        changed.append(f"enabled={r.enabled}")
    if not changed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма подадени промени.")
    await session.commit()
    await log_action(session, actor, "shipping.rate", target=f"{r.courier}/{r.mode}/{r.max_kg}kg",
                     detail=" · ".join(changed))
    return {"ok": True}


@router.post("/api/shipping-rates/refresh")
async def refresh_now(actor: User = Depends(require_roles("manager")),
                      session: AsyncSession = Depends(get_async_session)) -> dict:
    out = await refresh_rates()
    await log_action(session, actor, "shipping.refresh", target="rates",
                     detail=" · ".join(f"{k}: {v}" for k, v in out.items()))
    return {"ok": True, "couriers": out}


# ------------------------------------------------------------------ public (checkout) ---
def _check_token(token: str | None) -> None:
    """Same shared-secret model as the office lookup: empty config = endpoint DISABLED."""
    configured = (get_settings().office_lookup_token or "").strip()
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Shipping rates are disabled (no token set).")
    if not token or not hmac.compare_digest(token.strip(), configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing office-lookup token.")


@router.get("/api/public/shipping-rates")
async def public_rates(
    background: BackgroundTasks,
    weight_kg: float = Query(1.0, gt=0, le=100),
    cod: int = Query(0, ge=0, le=1),
    x_office_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _check_token(x_office_token)
    rows = (await session.execute(select(ShippingRate).where(
        ShippingRate.enabled.is_(True)))).scalars().all()
    # staleness watch: serve what we have, refresh behind the scenes when old (never inline —
    # checkout must not wait on five quote APIs)
    newest = max((r.refreshed_at for r in rows if r.refreshed_at), default=None)
    if newest is not None and newest.tzinfo is None:
        newest = newest.replace(tzinfo=_dt.UTC)   # SQLite drops tz on the way back
    if newest is None or (_dt.datetime.now(_dt.UTC) - newest).total_seconds() > STALE_AFTER_H * 3600:
        background.add_task(refresh_rates)
    best: dict = {}
    for r in rows:
        key = (r.courier, r.mode)
        cur = best.get(key)
        # smallest bracket that covers the weight; heavier-than-all falls back to the LARGEST
        # bracket (multi-parcel risk documented in the design; analytics will show frequency)
        covers = r.max_kg >= weight_kg
        if cur is None:
            best[key] = r
            continue
        cur_covers = cur.max_kg >= weight_kg
        if covers and (not cur_covers or r.max_kg < cur.max_kg):
            best[key] = r
        elif not covers and not cur_covers and r.max_kg > cur.max_kg:
            best[key] = r
    rates, unpriced = [], []
    for (name, mode), r in best.items():
        # A row with no tariff would price at the BUFFER ALONE — 10 to 50 cents of shipping on
        # a real order. The refresh cannot create such a row today, which is precisely why
        # nothing would have caught it if it ever did. Drop it and say so; a carrier missing
        # from the checkout is a visible problem, a 0.30 € delivery is not.
        if not r.label_cost or r.label_cost <= 0:
            unpriced.append(f"{name}/{mode}")
            continue
        price = r.label_cost + r.buffer + (r.cod_fee if cod else 0.0)
        rates.append({"courier": name, "mode": mode,
                      "label_bg": f"{courier_mod.BG_LABELS.get(name, name)} — {_MODE_BG.get(mode, mode)}",
                      "price_eur": round(price, 2)})
    if unpriced:
        background.add_task(_flag_unpriced, tuple(unpriced))
    rates.sort(key=lambda x: (x["price_eur"], x["courier"]))    # cheapest first (owner OK)
    for i, rate in enumerate(rates, 1):
        rate["sort"] = i
    free_over, _ = await get_free_over(session)
    return {"rates": rates, "free_over_eur": free_over}


async def _flag_unpriced(keys: tuple) -> None:
    """An enabled carrier that checkout could not price is an ops issue, not a log line —
    somebody has to notice that an option vanished from the storefront."""
    await record_issue(source="panel", topic="shipping-rates", order_ref=",".join(keys),
                       severity="warning", kind="rates_missing_tariff",
                       summary="Включен куриер без тарифа — не се предлага на checkout.",
                       detail={"couriers": list(keys)})
