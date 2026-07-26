# -*- coding: utf-8 -*-
"""Insights for the Анализи module. Two faces:
  • /catalog — enrichment & launch-readiness, LIVE today (the catalog is rich)
  • /sales   — revenue/orders KPIs, empty until the store goes live
Both read the catalog DB read-only through darling_core."""
from fastapi import APIRouter, Depends

from ..core import analytics, db
from ..users import current_active_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _one(c, q, default=0):
    try:
        v = c.execute(q).fetchone()
        return (v[0] if v and v[0] is not None else default)
    except Exception:
        return default


@router.get("/catalog")
def catalog_readiness(user=Depends(current_active_user)):
    """Where the 14k-product migration actually stands — real numbers, now."""
    c = db.connect()
    try:
        total = _one(c, "SELECT COUNT(*) FROM products")
        needs_human = _one(c, "SELECT SUM(needs_human) FROM products")
        launch_ready = _one(c, "SELECT COUNT(*) FROM launch_readiness WHERE ready=1")
        launch_blocked = _one(c, "SELECT COUNT(*) FROM launch_readiness WHERE ready=0")
        scored = launch_ready + launch_blocked
        reviewed = _one(c, "SELECT COUNT(*) FROM review_state")
        review_ready = _one(c, "SELECT COUNT(*) FROM review_state WHERE ready=1")
        try:  # degrade to [] like every sibling query (via _one) if dim_product is absent
            suppliers = [
                {"supplier": s or "—", "count": n}
                for s, n in c.execute(
                    "SELECT supplier, COUNT(*) n FROM dim_product WHERE supplier<>'' "
                    "GROUP BY supplier ORDER BY n DESC LIMIT 8").fetchall()
            ]
        except Exception:
            suppliers = []
        latest_snapshot = _one(
            c, "SELECT MAX(snapshot_date) FROM price_history", default=None)
        avg_retail = round(_one(
            c, "SELECT AVG(retail_price) FROM price_history "
               "WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM price_history)"), 2)
    finally:
        c.close()

    return {
        "cards": {
            "total": total,
            "launch_ready": launch_ready,
            "launch_blocked": launch_blocked,
            "unscored": max(total - scored, 0),
            "needs_human": needs_human,
            "reviewed": reviewed,
            "review_ready": review_ready,
            "suppliers": len(suppliers),
            "avg_retail": avg_retail,
            "latest_snapshot": latest_snapshot,
        },
        # readiness funnel for a small stacked bar
        "readiness": [
            {"label": "Готови за старт", "value": launch_ready, "tone": "ready"},
            {"label": "Блокирани", "value": launch_blocked, "tone": "blocked"},
            {"label": "Непроверени", "value": max(total - scored, 0), "tone": "pending"},
        ],
        "suppliers": suppliers,
    }


@router.get("/sales")
def sales(user=Depends(current_active_user)):
    """Revenue/orders. Empty states until launch — the shape is here so it fills itself
    in the moment the first Shopify order lands."""
    return {
        "kpis": analytics.kpis(),
        "top_products": analytics.top_products(by="revenue", limit=10),
        "price_changes": analytics.price_changes(days=90, min_pct=5.0),
    }
