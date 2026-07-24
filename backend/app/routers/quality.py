# -*- coding: utf-8 -*-
"""Product-data quality — a whole-catalog audit + a single-product check. A second automated net on
top of the owner's manual audit. Logic lives in app/quality.py; this is the HTTP surface."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import quality
from ..core import barsy
from ..models import User
from ..upstream import upstream
from ..users import current_active_user

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/audit")
async def audit(
    user: User = Depends(current_active_user),
    limit: int = Query(2000, ge=1, le=5000, description="колко артикула да провери"),
    flagged_limit: int = Query(500, ge=1, le=2000, description="колко проблемни реда да върне"),
):
    """Audit the Barsy catalog for completeness + duplicates. Returns a summary and a flagged
    worklist. `limit` bounds how many articles are scanned (Barsy paging); if `total==limit` more
    may exist — raise the limit to cover the full catalog."""
    with upstream("Barsy каталог"):
        rows = await run_in_threadpool(barsy.call, "Articles_getlist", {"filters": {}, "length": limit})
    report = quality.audit_catalog(rows or [])
    truncated_flagged = report["flagged"][:flagged_limit]
    report["flagged_returned"] = len(truncated_flagged)
    report["flagged"] = truncated_flagged
    report["scan_truncated"] = report["total"] >= limit
    return report


class CheckBody(BaseModel):
    name: str = Field("", max_length=512)
    barcode: str = Field("", max_length=64)
    price: float | None = None
    category: str = Field("", max_length=255)
    description: str = Field("", max_length=8000)
    picture: str = Field("", max_length=2000)


@router.post("/check")
def check_one(body: CheckBody, user: User = Depends(current_active_user)):
    """Quality check for a single product being added (used by intake) — same rules as the audit."""
    issues = quality.check_article({
        "article_name": body.name, "barcode": body.barcode, "actual_price": body.price,
        "category": body.category, "description": body.description, "picture": body.picture,
    })
    return {"ok": not issues, "issues": issues}
