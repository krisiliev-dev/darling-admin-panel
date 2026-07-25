# -*- coding: utf-8 -*-
"""Каталог — browse + edit products straight from BARSY (the system of record). Approved
products auto-push to Barsy, so the panel requests them here. Price stays read-only (Microinvest-
sourced, never edited here — price policy). Editable: name, description, barcode."""
import base64
import urllib.request
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
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


# ------------------------------------------------------------------ product images (proxy) ---
# Barsy's image URLs are HTTP-Basic-auth-walled, so the browser can't <img src> them directly.
# We proxy: resolve the article's image via Files_getlist, fetch it with the service creds, stream
# the bytes back. The frontend points <img> at OUR endpoint. Bytes are cached in-process.
def _barsy_host() -> tuple[str, str, str]:
    base, user, pw = barsy._env()                 # base = https://<host>/endpoints/json
    return base.split("/endpoints/")[0], user, pw


@lru_cache(maxsize=512)
def _fetch_image(article_id: int, size: str):
    """(bytes, content_type) for an article's first image, or None. Cached — repeat loads (a catalog
    page re-render) don't re-hit the low-tier Barsy."""
    files = barsy.call("Files_getlist", {"obj_type": "articles", "obj_id": article_id}) or []
    if not isinstance(files, list) or not files:
        return None
    f = files[0]
    rel = f.get("thumbnail_url") if size == "thumb" else f.get("url")
    rel = rel or f.get("url")
    if not rel:
        return None
    if size == "thumb":
        rel = rel.replace("width=50", "width=200")   # the default 50px thumb is too small for cards
    host, user, pw = _barsy_host()
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
    req = urllib.request.Request(host + rel)
    req.add_header("Authorization", "Basic " + tok)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), (f.get("type") or "image/png")


@router.get("/image/{article_id}")
async def product_image(article_id: int, size: str = Query("thumb"),
                        user: User = Depends(current_active_user)):
    """Proxy an article's Barsy image (thumb|full). 404 → the frontend shows a placeholder."""
    try:
        res = await run_in_threadpool(_fetch_image, article_id, "full" if size == "full" else "thumb")
    except Exception:
        res = None
    if not res:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма изображение.")
    data, ctype = res
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=86400"})


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
        rows = barsy.call("Articles_getlist",
                          {"filters": filters, "length": limit, "extra_properties": {"picture_url": 1}})
    stock = _stock_map()
    items = []
    for a in (rows or [])[:limit]:
        st = stock.get(a.get("article_id"), {})
        if low_only and not st.get("low"):
            continue
        items.append({"article_id": a.get("article_id"), "code": a.get("code") or "",
                      "name": a.get("article_name") or "", "price": a.get("actual_price"),
                      "stock": st.get("amount"), "min_stock": st.get("min"), "low": bool(st.get("low")),
                      "has_image": bool(a.get("picture_url"))})
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
