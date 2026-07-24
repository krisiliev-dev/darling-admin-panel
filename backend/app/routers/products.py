# -*- coding: utf-8 -*-
"""Read-only catalog list — a window onto the 9k+ products for the panel.
Writes (intake/enrichment approval) come in Phase 2."""
from fastapi import APIRouter, Depends, Query

from ..core import db
from ..users import current_active_user

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
def list_products(
    user=Depends(current_active_user),
    q: str = Query("", description="търсене по код или име"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    c = db.connect()
    try:
        where, args = "", []
        if q:
            where = "WHERE d.code LIKE ? OR d.name LIKE ?"
            args = [f"%{q}%", f"%{q}%"]
        total = c.execute(
            f"SELECT COUNT(*) FROM dim_product d {where}", args).fetchone()[0]
        rows = c.execute(
            "SELECT d.code, d.name, d.brand, d.category_leaf, d.supplier, "
            "       p.needs_human, "
            "       (SELECT ph.retail_price FROM price_history ph "
            "        WHERE ph.code=d.code ORDER BY ph.snapshot_date DESC LIMIT 1) AS price, "
            "       lr.ready AS launch_ready "
            "FROM dim_product d "
            "LEFT JOIN products p ON p.code=d.code "
            "LEFT JOIN launch_readiness lr ON lr.code=d.code "
            f"{where} ORDER BY d.name LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
    finally:
        c.close()

    items = [{
        "code": r[0], "name": r[1] or r[0], "brand": r[2] or "",
        "category": r[3] or "", "supplier": r[4] or "",
        "needs_human": bool(r[5]), "price": r[6],
        "launch_ready": (None if r[7] is None else bool(r[7])),
    } for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}
