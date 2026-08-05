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
from ..models import ShippingRate, User
from ..users import require_roles

router = APIRouter(tags=["shipping-rates"])

BRACKETS = [1.0, 3.0, 5.0, 10.0]          # kg — from the live matrix
FREE_OVER_EUR = 77.0                       # owner decision 2026-08-05 (config page in slice 2)
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
    rows = (await session.execute(select(ShippingRate).order_by(
        ShippingRate.courier, ShippingRate.mode, ShippingRate.max_kg))).scalars().all()
    return {"rates": [{
        "id": r.id, "courier": r.courier, "mode": r.mode, "max_kg": r.max_kg,
        "label_cost": r.label_cost, "cod_fee": r.cod_fee, "buffer": r.buffer,
        "enabled": r.enabled,
        "refreshed_at": r.refreshed_at.isoformat() if r.refreshed_at else None,
    } for r in rows], "free_over_eur": FREE_OVER_EUR}


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
    rates = []
    for (name, mode), r in best.items():
        price = r.label_cost + r.buffer + (r.cod_fee if cod else 0.0)
        rates.append({"courier": name, "mode": mode,
                      "label_bg": f"{courier_mod.BG_LABELS.get(name, name)} — {_MODE_BG.get(mode, mode)}",
                      "price_eur": round(price, 2)})
    rates.sort(key=lambda x: (x["price_eur"], x["courier"]))    # cheapest first (owner OK)
    for i, rate in enumerate(rates, 1):
        rate["sort"] = i
    return {"rates": rates, "free_over_eur": FREE_OVER_EUR}
