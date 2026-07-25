# -*- coding: utf-8 -*-
"""Каталог — browse + edit products straight from BARSY (the system of record). Approved
products auto-push to Barsy, so the panel requests them here. Price stays read-only (Microinvest-
sourced, never edited here — price policy). Editable: name, description, barcode."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy
from ..db import get_async_session
from ..models import User
from ..upstream import upstream
from ..users import current_active_user, require_roles
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _stock_map() -> dict:
    """{article_id -> {amount, min, low}} from Barsy Store_amounts — folds live stock (Наличности)
    into the catalogue list so it's one product view. Best-effort: empty if Barsy stock is empty."""
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    try:
        out = barsy.call("Store_amounts", {"action_type": "values", "page_num": 1, "rows": 500,
                                           "filters": {}}) or {}
    except Exception:
        return {}
    m: dict = {}
    for r in (out.get("rows") or []):
        aid = r.get("article_id")
        if aid is None:
            continue
        amount, minimum = _num(r.get("store_amount")), _num(r.get("min_amount"))
        low = (r.get("below_min") == 1) or (minimum is not None and amount is not None and amount <= minimum)
        m[aid] = {"amount": amount, "min": minimum, "low": bool(low)}
    return m


@router.get("")
def list_articles(
    user: User = Depends(current_active_user),
    q: str = Query(""), limit: int = Query(40, ge=1, le=100),
    low_only: bool = Query(False, description="само артикули с ниска наличност"),
):
    """Browse/search the Barsy catalog WITH live stock folded in (fills as products push to Barsy)."""
    filters: dict = {}
    if q:
        filters["code" if q.strip().isdigit() else "article_name"] = q.strip()
    with upstream("Barsy каталог"):
        rows = barsy.call("Articles_getlist", {"filters": filters, "length": limit})
    stock = _stock_map()
    items = []
    for a in (rows or [])[:limit]:
        st = stock.get(a.get("article_id"), {})
        if low_only and not st.get("low"):
            continue
        items.append({"article_id": a.get("article_id"), "code": a.get("code") or "",
                      "name": a.get("article_name") or "", "price": a.get("actual_price"),
                      "stock": st.get("amount"), "min_stock": st.get("min"), "low": bool(st.get("low"))})
    return {"items": items}


@router.get("/{article_id}")
def get_article(article_id: int, user: User = Depends(current_active_user)):
    """Full product detail from Barsy."""
    with upstream("Barsy артикул"):
        a = barsy.call("Articles_get", {"article_id": article_id})
    if not isinstance(a, dict) or not a.get("article_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Артикулът не е намерен.")
    bc = a.get("barcode")
    if isinstance(bc, list):
        bc = bc[0] if bc else ""
    return {
        "article_id": a.get("article_id"), "code": a.get("code") or "",
        "name": a.get("article_name") or "", "description": a.get("description") or "",
        "price": a.get("actual_price"), "barcode": bc or "",
        "unit": a.get("amount_type_name_short") or "",
        "category": a.get("master_cat") or (a.get("categories_names") or ""),
    }


class ArticleEdit(BaseModel):
    name: str
    description: str = ""
    barcode: str = ""


@router.post("/{article_id}")
async def update_article(
    article_id: int, req: ArticleEdit,
    actor: User = Depends(require_roles("staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Update editable fields in Barsy (name/description/barcode). Price is NOT touched here."""
    if not req.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Името е задължително.")
    article: dict = {
        "article_name": req.name.strip(),
        "article_name_ml": {"bg_BG": req.name.strip()},
    }
    if req.description:
        article["description_ml"] = {"bg_BG": req.description}
    if req.barcode.strip():
        article["barcode"] = req.barcode.strip()  # string, not array (Barsy create/update)
    with upstream("Barsy обновяване"):
        await run_in_threadpool(barsy.call, "Articles_update",
                                {"article_id": article_id, "article": article})
    await log_action(session, actor, "article.update", target=str(article_id),
                     detail=f"name={req.name[:40]}")
    return {"ok": True}
