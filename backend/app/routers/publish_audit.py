# -*- coding: utf-8 -*-
"""Pre-launch publish-readiness audit (#81). Every SELLABLE Shopify variant must be launch-ready:
  · SKU present  · SKU == a Barsy code (the join key — a null/unknown SKU means orders won't book)
  · inventory TRACKED  · product ACTIVE (published)  · policy DENY (or CONTINUE for backorderable)
Surfaced by the COD run: the migrated catalogue is largely DRAFT + untracked, and orders refuse on a null
SKU. This gives staff the exact "what to fix before publishing" list. Read-only.

The connector owns the Shopify variant enumeration (`list_sellable_variants()` — it knows the current
GraphQL shapes); this router cross-checks each SKU against the Barsy-code mirror (product_cache) and scores
readiness. `on_hand` (loaded per product at launch) isn't part of the pass/fail — the stock-load step sets it."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..core import connector
from ..db import get_async_session
from ..models import ProductCache, User
from ..users import current_active_user

router = APIRouter(prefix="/api/publish-audit", tags=["publish-audit"])

# issues that BLOCK publishing (policy_continue is a note, not a blocker — some products are backorderable)
_BLOCKERS = {"no_sku", "sku_not_in_barsy", "not_tracked", "not_active"}


def _issues(v: dict, barsy_codes: set[str]) -> list[str]:
    out: list[str] = []
    sku = str(v.get("sku") or "").strip()
    if not sku:
        out.append("no_sku")
    elif sku not in barsy_codes:
        out.append("sku_not_in_barsy")
    if not v.get("tracked"):
        out.append("not_tracked")
    if str(v.get("product_status") or "").upper() != "ACTIVE":
        out.append("not_active")
    if str(v.get("policy") or "").upper() == "CONTINUE":
        out.append("policy_continue")   # note only: backorderable — allowed, but flag so it's intentional
    return out


@router.get("")
async def publish_audit(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Report of variants not ready to publish, grouped by issue. `ready` counts variants with no
    BLOCKING issue (a CONTINUE-policy note doesn't block)."""
    codes = {c for c in (await session.execute(select(ProductCache.code))).scalars().all() if c}
    try:
        variants = await run_in_threadpool(connector.list_sellable_variants)
    except AttributeError:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED,
                            "Одитът не е наличен (connector.list_sellable_variants липсва).")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Shopify недостъпен: {str(e)[:160]}")

    rows = []
    by_issue: dict[str, int] = {}
    ready = 0
    for v in (variants or []):
        iss = _issues(v, codes)
        blocking = [i for i in iss if i in _BLOCKERS]
        if not blocking:
            ready += 1
        for i in iss:
            by_issue[i] = by_issue.get(i, 0) + 1
        if iss:
            rows.append({
                "variant_id": v.get("variant_id"), "sku": v.get("sku") or "",
                "product_title": v.get("product_title") or "", "product_status": v.get("product_status") or "",
                "tracked": bool(v.get("tracked")), "policy": v.get("policy") or "",
                "on_hand": v.get("on_hand"), "issues": iss, "blocked": bool(blocking),
            })
    total = len(variants or [])
    return {"total": total, "ready": ready, "not_ready": total - ready,
            "by_issue": by_issue, "variants": rows}
