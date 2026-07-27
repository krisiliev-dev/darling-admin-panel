# -*- coding: utf-8 -*-
"""Public (token-authed) courier office lookup — for the storefront's Cloudflare Worker to populate
the checkout office/locker picker. Read-only: city → [{id, name, address, city}]. The courier API
credentials stay SERVER-SIDE in the panel and are never exposed; the caller only presents a shared
secret (X-Office-Token == OFFICE_LOOKUP_TOKEN). No panel-user auth — this is the one deliberately
external entry point. Empty token in config => the endpoint is DISABLED (403), so it stays off until
the owner sets the secret. Owner-approved 2026-07-27."""
import hmac

from fastapi import APIRouter, Header, HTTPException, Query, status

from ..config import get_settings
from ..core import courier as courier_mod
from ..upstream import upstream

router = APIRouter(prefix="/api/public/couriers", tags=["public-couriers"])

# couriers whose office/locker networks make sense for an online office-picker
_ALLOWED = {"speedy", "econt", "boxnow", "sameday", "pigeon", "evropat"}


def _check_token(token: str | None) -> None:
    """Constant-time check of the shared secret. Disabled (403) when no token is configured."""
    configured = (get_settings().office_lookup_token or "").strip()
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Office lookup is disabled (no token set).")
    if not token or not hmac.compare_digest(token.strip(), configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing office-lookup token.")


@router.get("/{key}/offices")
def public_offices(key: str, city: str = Query("", max_length=120, description="filter by city"),
                   x_office_token: str | None = Header(default=None)):
    """City → courier office/locker list for the storefront checkout picker. Token-gated; courier
    credentials never leave the panel. Returns the same shape as the internal picker:
    [{id, name, address, city}]."""
    _check_token(x_office_token)
    k = (key or "").lower()
    if k not in _ALLOWED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown courier.")
    try:
        with upstream(k):
            offices = courier_mod.get(k).offices(city=city or None)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"courier lookup failed: {str(e)[:120]}")
    return {"courier": k, "count": len(offices), "offices": offices[:300]}
