# -*- coding: utf-8 -*-
"""Public (token-authed) courier office lookup — for the storefront's Cloudflare Worker to populate
the checkout office/locker picker. Read-only: city → [{id, name, address, city}]. The courier API
credentials stay SERVER-SIDE in the panel and are never exposed; the caller only presents a shared
secret (X-Office-Token == OFFICE_LOOKUP_TOKEN). No panel-user auth — this is the one deliberately
external entry point. Empty token in config => the endpoint is DISABLED (403), so it stays off until
the owner sets the secret. Owner-approved 2026-07-27."""
import hmac
import math
import time

from fastapi import APIRouter, Header, HTTPException, Query, status

from ..config import get_settings
from ..core import courier as courier_mod
from ..upstream import upstream

router = APIRouter(prefix="/api/public/couriers", tags=["public-couriers"])

# couriers with a WORKING office/locker lookup, i.e. an implemented offices() AND credentials
# (or, for boxnow, a public feed that needs none).
#
# boxnow joined on 2026-08-06: its adapter has been live since the real test waybill, and the
# comment that used to sit here still called it a stub — which is why the storefront's автомат
# picker had nothing to talk to. Its locker list comes from BoxNow's PUBLIC locations feed, so
# it works with no keys at all.
#
# sameday is deliberately NOT here. Its adapter exists but the API credentials have not been
# issued, so every lookup would fail upstream and the picker would show a 502 where a carrier
# should be. Add it the day the keys land and the five VERIFY-LIVE checks in docs/SAMEDAY.md
# are done.
# pigeon: adapter + keys are LIVE (courier.py) but Pigeon has no office in Svishtov yet, so
# the shop cannot ship with them ??? owner 2026-08-05: keep the carrier DARK on the storefront
# (class-carriers.php offers whatever the panel serves). Re-add "pigeon" here the moment
# their Svishtov office opens + PIGEON_PICKUP_OFFICE_ID is set.
_ALLOWED = {"speedy", "econt", "boxnow"}


_OFFICE_TTL = 900.0          # cache the full office list per courier (office networks change rarely)
_OFFICE_CACHE: dict = {}     # {courier: (fetched_at_monotonic, [offices])}
_CAP = 300                   # max offices returned per response


def _check_token(token: str | None) -> None:
    """Constant-time check of the shared secret. Disabled (403) when no token is configured."""
    configured = (get_settings().office_lookup_token or "").strip()
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Office lookup is disabled (no token set).")
    if not token or not hmac.compare_digest(token.strip(), configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing office-lookup token.")


def _norm(s: str) -> str:
    """Case- + whitespace-insensitive key so a Cyrillic city query matches the stored city/name."""
    return "".join((s or "").split()).casefold()


def _all_offices(k: str) -> list:
    """Full office list for a courier, cached — we filter by city in-process because the courier
    APIs' `name` param filters by OFFICE name, not city (that was the ?city=София→0 bug)."""
    hit = _OFFICE_CACHE.get(k)
    if hit and (time.monotonic() - hit[0]) < _OFFICE_TTL:
        return hit[1]
    with upstream(k):
        offices = courier_mod.get(k).offices(city=None) or []
    _OFFICE_CACHE[k] = (time.monotonic(), offices)
    return offices


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres. Good to a few metres over Bulgaria, which is far
    better than a picker needs, and it costs nothing."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _filter_offices(offices: list, city: str = "", lat: float | None = None,
                    lon: float | None = None, radius_km: float | None = None) -> tuple[list, dict]:
    """Filter and rank, WITHOUT applying the response cap.

    The cap belongs to the caller and must come last. Capping the source list and filtering
    the remainder is how `?city=София` once returned a single office: the slice was
    alphabetical and cut Sofia off before the filter ever ran. On a map the same mistake is
    worse, because the row it drops is precisely the locker nearest the customer.

    Offices with no coordinates are never silently lost. A radius filter cannot include them
    — we cannot prove they are near — so they are excluded and COUNTED; without a radius they
    are appended after the ranked ones, since they can be shown but not ranked.
    """
    rows = list(offices)
    if city.strip():
        q = _norm(city)
        rows = [o for o in rows
                if q in _norm(o.get("city") or "") or q in _norm(o.get("name") or "")]
    if lat is None or lon is None:
        return rows, {}

    ranked, unpositioned = [], []
    for o in rows:
        olat, olon = o.get("lat"), o.get("lon")
        if olat is None or olon is None:
            unpositioned.append(o)
            continue
        ranked.append({**o, "distance_km": round(_haversine_km(lat, lon, float(olat), float(olon)), 2)})
    if radius_km is not None:
        ranked = [o for o in ranked if o["distance_km"] <= radius_km]
    ranked.sort(key=lambda o: o["distance_km"])
    if radius_km is None:
        ranked.extend(unpositioned)
        return ranked, {"no_position": len(unpositioned)}
    return ranked, {"no_position": len(unpositioned)}


@router.get("/{key}/offices")
def public_offices(
    key: str,
    city: str = Query("", max_length=120, description="filter by city"),
    lat: float | None = Query(None, ge=-90, le=90, description="customer latitude — ranks nearest first"),
    lon: float | None = Query(None, ge=-180, le=180, description="customer longitude"),
    radius_km: float | None = Query(None, gt=0, le=500, description="keep only offices within this many km of lat/lon"),
    x_office_token: str | None = Header(default=None),
):
    """Courier office/locker list for the storefront checkout picker. Token-gated; courier
    credentials never leave the panel.

    Returns [{id, name, address, city, lat, lon, kind}] — `kind` is "locker" (автомат) or
    "office", and lat/lon are floats or null. Pass lat+lon to rank nearest-first and get a
    `distance_km` on every positioned row; add radius_km to filter as well. `count` is the
    size of the FILTERED set and `capped` says whether the returned list was truncated, so a
    caller can tell "these are all of them" from "here are the first _CAP".
    """
    _check_token(x_office_token)
    k = (key or "").lower()
    if k not in _ALLOWED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown courier.")
    if (lat is None) != (lon is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lat and lon must be given together.")
    if radius_km is not None and lat is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "radius_km needs lat and lon.")
    try:
        offices = _all_offices(k)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"courier lookup failed: {str(e)[:120]}")
    rows, meta = _filter_offices(offices, city, lat, lon, radius_km)
    # THE CAP GOES LAST. See _filter_offices.
    return {"courier": k, "count": len(rows), "capped": len(rows) > _CAP,
            "offices": rows[:_CAP], **meta}


@router.get("/{key}/quote")
def public_quote(
    key: str,
    dest_type: str = Query(..., pattern="^(office|door)$", description="office | door"),
    office_id: str = Query("", max_length=32, description="required for dest_type=office"),
    city: str = Query("", max_length=120, description="required for dest_type=door"),
    address: str = Query("", max_length=200, description="street + no (door)"),
    postcode: str = Query("", max_length=16),
    weight_g: int = Query(..., ge=1, le=100_000, description="parcel weight in grams"),
    cod: bool = Query(False, description="наложен платеж — may add a collection surcharge"),
    amount_eur: float = Query(0.0, ge=0, description="order value (for the COD collection fee)"),
    x_office_token: str | None = Header(default=None),
) -> dict:
    """Live delivery-fee quote for the storefront checkout: weight + destination → fee, computed via the
    courier's own rate API (no static Shopify rate). Token-gated like the office lookup; courier creds
    stay server-side. Returns shipping + COD-surcharge broken out. The COD surcharge is derived as the
    delta between a with-COD quote and a prepaid quote (adapters return one total), so we make two
    upstream calls only when cod=true. `estimated:true` always — it's a checkout estimate, not the
    booked label price."""
    _check_token(x_office_token)
    k = (key or "").lower()
    if k not in _ALLOWED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown courier.")
    if dest_type == "office" and not office_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "office_id is required for dest_type=office.")
    if dest_type == "door" and not city.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "city is required for dest_type=door.")

    weight_kg = max(int(weight_g), 1) / 1000.0

    def _shipment(cod_amount: float):
        kwargs: dict = {"order_ref": "quote", "recipient": "Оценка", "phone": "0000000000",
                        "weight_kg": weight_kg, "cod_amount": cod_amount}
        if dest_type == "office":
            kwargs["office_id"] = office_id.strip()
        else:
            kwargs["address"] = {"city": city.strip(), "postcode": postcode.strip(), "street": address.strip()}
        return courier_mod.Shipment(**kwargs)

    try:
        with upstream(k):
            c = courier_mod.get(k)
            base = c.calculate(_shipment(0.0))                    # prepaid → base shipping
            shipping = round(float(base.get("price") or 0), 2)
            cod_fee, total = 0.0, shipping
            if cod and amount_eur > 0:
                full = c.calculate(_shipment(float(amount_eur)))  # with COD → total incl. surcharge
                total = round(float(full.get("price") or 0), 2)
                cod_fee = round(max(total - shipping, 0.0), 2)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"quote failed: {str(e)[:120]}")

    return {
        "carrier": k, "currency": "EUR",
        "shipping_fee_eur": shipping,
        "cod_fee_eur": cod_fee,
        "total_fee_eur": round(shipping + cod_fee, 2),
        "estimated": True,
        "meta": {"weight_g": int(weight_g), "dest_type": dest_type,
                 "office_id": office_id.strip() or None, "city": city.strip() or None},
    }
