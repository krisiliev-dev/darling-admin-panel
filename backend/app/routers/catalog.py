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
from ..users import current_active_user, current_owner, require_roles
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


# ------------------------------------------------------------------ local product cache ---
# A SQLite mirror of Barsy's catalogue (product_cache) so search/filter is instant and doesn't hit
# the low-tier Barsy per keystroke — and so attribute filters + bulk ops work over a selected set.
import datetime as _dt
import json as _json

from sqlalchemy import Float, cast, select

from ..models import ProductCache


def _numf(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_attributes(a: dict) -> list[dict]:
    """Barsy article_details = {idx: {key, value, key_id, value_id}} → a flat attribute list."""
    det = a.get("article_details")
    if not isinstance(det, dict):
        return []
    out = []
    for v in det.values():
        if isinstance(v, dict) and v.get("key"):
            out.append({"key": str(v.get("key")), "value": str(v.get("value") or ""),
                        "key_id": v.get("key_id"), "value_id": v.get("value_id")})
    return out


def _sync_fetch() -> list[dict]:
    """Blocking: pull ALL articles + their attributes/stock/picture from Barsy in ONE bulk call
    (extra_properties), enriching supplier from the local code map as a fallback. Paginated to keep
    each page light on the low-tier server."""
    from .zayavki import _supplier_catalog
    by_code = _supplier_catalog()["by_code"]
    extra = {"article_details": 1, "picture_url": 1, "has_attributes": 1}
    rows: list = []
    page = 1
    while page <= 40:                                    # hard bound (~40k) — safety, not expected
        batch = barsy.call("Articles_getlist",
                           {"filters": {}, "length": 1000, "page_num": page, "extra_properties": extra}) or []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    stock = _stock_map()
    out = []
    for a in rows:
        aid = a.get("article_id")
        if aid is None:
            continue
        code = str(a.get("code") or "")
        st = stock.get(aid, {})
        sups = list((by_code.get(code) or {}).keys())
        bc = a.get("barcode")
        out.append({
            "article_id": aid, "code": code, "name": a.get("article_name") or "",
            "barcode": (bc if isinstance(bc, str) else (bc[0] if isinstance(bc, list) and bc else "")) or "",
            "category": a.get("category_name") or a.get("category") or "",
            "supplier": sups[0] if sups else "",
            "price": _numf(a.get("actual_price")), "stock": st.get("amount"), "min_stock": st.get("min"),
            "low": bool(st.get("low")), "has_image": bool(a.get("picture_url")),
            "attributes": _parse_attributes(a),
        })
    return out


@router.post("/sync")
async def sync_cache(actor: User = Depends(require_roles("staff")),
                     session: AsyncSession = Depends(get_async_session)):
    """Rebuild the local product cache from Barsy (manual trigger; also run nightly). One big
    Articles_getlist + one Store_amounts, then upsert. Write-through keeps it fresh between syncs."""
    data = await run_in_threadpool(_sync_fetch)
    existing = {p.article_id: p for p in (await session.execute(select(ProductCache))).scalars().all()}
    now = _dt.datetime.now(_dt.timezone.utc)
    for d in data:
        p = existing.get(d["article_id"]) or ProductCache(article_id=d["article_id"])
        p.code, p.name, p.barcode = d["code"], d["name"], d["barcode"]
        p.category, p.supplier = d["category"], d["supplier"]
        p.price, p.stock, p.min_stock, p.low = d["price"], d["stock"], d["min_stock"], d["low"]
        p.has_image = d["has_image"]
        p.attributes_json = _json.dumps(d["attributes"], ensure_ascii=False)
        # universal free-text key — name, code, barcode, supplier AND price (Hermes: keep the cache
        # as search source; just widen the index, incl. price at 2dp so "19.90" or "19.9" both match)
        price_s = f"{d['price']:.2f}" if d["price"] is not None else ""
        p.search_lc = f"{d['name']} {d['code']} {d['barcode']} {d['supplier']} {price_s}".lower()
        p.attr_lc = "|".join(f"{x['key']}={x['value']}" for x in d["attributes"]).lower()
        p.updated_at = now
        session.add(p)
    await session.commit()
    await log_action(session, actor, "catalog.sync", detail=f"cached={len(data)}")
    return {"synced": len(data)}


@router.get("/cache-status")
async def cache_status(user: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    from sqlalchemy import func as _func
    total = (await session.execute(select(_func.count()).select_from(ProductCache))).scalar() or 0
    last = (await session.execute(select(_func.max(ProductCache.updated_at)))).scalar()
    return {"count": total, "last_sync": last.isoformat() if last else None}


@router.get("/search")
async def search_cache(
    user: User = Depends(current_active_user),
    q: str = Query(""), supplier: str = Query(""), category: str = Query(""),
    price_min: float | None = Query(None), price_max: float | None = Query(None),
    low_only: bool = Query(False), attr: list[str] = Query([]),
    sort: str = Query("name"), order: str = Query("asc"),
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
):
    """INSTANT filtered search over the local cache (no Barsy round-trip). `attr` repeats as
    "Ключ=Стойност" (e.g. attr=Цвят=Червен&attr=Материал=Стъкло) — combined with AND."""
    stmt = select(ProductCache)
    if q.strip():
        stmt = stmt.where(ProductCache.search_lc.like(f"%{q.strip().lower()}%"))  # Cyrillic-safe
    if supplier:
        stmt = stmt.where(ProductCache.supplier == supplier)
    if category:
        stmt = stmt.where(ProductCache.category == category)
    if price_min is not None:
        stmt = stmt.where(cast(ProductCache.price, Float) >= price_min)
    if price_max is not None:
        stmt = stmt.where(cast(ProductCache.price, Float) <= price_max)
    if low_only:
        stmt = stmt.where(ProductCache.low.is_(True))
    for a in attr:                                   # each "key=value" narrows further (AND)
        if a.strip():
            stmt = stmt.where(ProductCache.attr_lc.like(f"%{a.strip().lower()}%"))
    from sqlalchemy import func as _func
    total = (await session.execute(select(_func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = {"name": ProductCache.name, "code": ProductCache.code, "price": ProductCache.price,
           "stock": ProductCache.stock, "supplier": ProductCache.supplier}.get(sort, ProductCache.name)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(stmt)).scalars().all()
    return {"total": total, "page": page, "per_page": per_page, "items": [
        {"article_id": p.article_id, "code": p.code, "name": p.name, "barcode": p.barcode,
         "category": p.category, "supplier": p.supplier, "price": p.price, "stock": p.stock,
         "low": p.low, "has_image": p.has_image,
         "attributes": _json.loads(p.attributes_json or "[]")} for p in rows]}


class BulkPriceRequest(BaseModel):
    article_ids: list[int]
    op: str                      # mul | div | add | sub | set
    value: float
    round_to: int = 2
    confirm: bool = False


def _apply_op(old: float | None, op: str, value: float) -> float:
    o = old if old is not None else 0.0
    n = {"mul": o * value, "div": (o / value if value else o), "add": o + value,
         "sub": o - value, "set": value}[op]
    return max(0.0, n)


@router.post("/bulk-price")
async def bulk_price(req: BulkPriceRequest, actor: User = Depends(current_owner),
                     session: AsyncSession = Depends(get_async_session)):
    """Bulk change the SELL price (actual_price) of the selected products. Preview first (confirm=
    false → computed new prices, writes nothing); confirm=true writes each via Articles_update and
    updates the cache. Owner/manager only, audited. (Permanent base change — the panel is source of
    truth for price post-Microinvest-migration; timed promos use Pricelists separately.)"""
    if req.op not in ("mul", "div", "add", "sub", "set"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна операция.")
    if not req.article_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма избрани продукти.")
    if req.op == "div" and req.value == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Деление на нула.")
    rd = max(0, min(4, req.round_to))
    rows = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(req.article_ids)))).scalars().all()}
    changes = []
    for aid in req.article_ids:
        p = rows.get(aid)
        if not p:
            continue
        new = round(_apply_op(p.price, req.op, req.value), rd)
        changes.append({"article_id": aid, "code": p.code, "name": p.name,
                        "old_price": p.price, "new_price": new})
    if not req.confirm:
        return {"dry_run": True, "count": len(changes), "changes": changes}

    applied, failed = 0, []
    for c in changes:
        try:
            await run_in_threadpool(barsy.call, "Articles_update",
                                    {"article_id": c["article_id"],
                                     "article": {"actual_price": str(c["new_price"])}})
        except Exception:
            failed.append(c["code"])
            continue
        p = rows[c["article_id"]]
        p.price = c["new_price"]                  # write-through to the cache
        applied += 1
    await session.commit()
    await log_action(session, actor, "catalog.bulk_price",
                     detail=f"op={req.op} value={req.value} applied={applied} failed={len(failed)}")
    return {"dry_run": False, "count": len(changes), "applied": applied, "failed": failed}


class PromoRequest(BaseModel):
    article_ids: list[int]
    name: str
    mode: str = "discount_pct"          # discount_pct | discount_eur | exact
    value: float
    date_from: str                       # YYYY-MM-DD
    date_to: str
    confirm: bool = False


_PROMO_MOD = {"discount_pct": 2, "discount_eur": 4, "exact": 5}   # Barsy modificator_type


def _period(date_from: str, date_to: str) -> str:
    """YYYY-MM-DD range → Barsy periods_date "d.m-d.m" (may cross year-end)."""
    def dm(s: str) -> str:
        parts = s.strip().split("-")
        if len(parts) == 3:            # YYYY-MM-DD
            return f"{int(parts[2])}.{int(parts[1])}"
        raise ValueError("дата трябва да е ГГГГ-ММ-ДД")
    return f"{dm(date_from)}-{dm(date_to)}"


@router.post("/promo")
async def create_promo(req: PromoRequest, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    """Create a NATIVE Barsy timed promotion (Pricelists rule) on the selected products — a % or €
    discount, or an exact promo price, active over a date window. Barsy adjusts current_price for the
    window and auto-reverts; the base actual_price is untouched. Owner-only, audited."""
    if not req.article_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма избрани продукти.")
    if req.mode not in _PROMO_MOD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден тип промоция.")
    if not req.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дай име на промоцията.")
    try:
        period = _period(req.date_from, req.date_to)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if not req.confirm:
        return {"dry_run": True, "count": len(req.article_ids), "period": period,
                "name": req.name, "mode": req.mode, "value": req.value}

    values = {
        "pricelist_name": req.name.strip(), "pricelist_type_id": 6, "pricelist_group_id": 1,
        "weight": 1, "periods_date": period, "begin_price_type": 1, "price_tax_mode": 1,
        "price_tax_id": 2, "modificator_type": _PROMO_MOD[req.mode], "modificator_value": req.value,
        "modificator_round_type": 0, "price_change_groups": [1], "total_period": "current",
        "price_type": 1,
    }
    try:
        await run_in_threadpool(barsy.call, "Pricelists_edit",
                                {"action_type": "save", "active_struct_id": "eStructForm_1", "values": values})
        idx = await run_in_threadpool(barsy.call, "Pricelists_index", {"action_type": "values"}) or {}
        rows = idx.get("rows", idx) if isinstance(idx, dict) else idx
        pid = None
        for r in (rows or []):
            if str(r.get("pricelist_name")) == req.name.strip():
                pid = r.get("pricelist_id") if pid is None else max(pid, r.get("pricelist_id"))
        if pid is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Промоцията е създадена, но ID не се разчете — провери в Barsy.")
        attach_rows = [{"article_id": aid, "req_amount": 1, "price_change_group": 1} for aid in req.article_ids]
        await run_in_threadpool(barsy.call, "Pricelists_save_articles", {"id": pid, "rows": attach_rows})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy (промоция): {str(e)[:180]}")
    await log_action(session, actor, "catalog.promo", target=req.name.strip(),
                     detail=f"pricelist={pid} mode={req.mode} value={req.value} period={period} n={len(req.article_ids)}")
    return {"dry_run": False, "pricelist_id": pid, "count": len(req.article_ids), "period": period}


def _cache_fields(a: dict) -> dict:
    """Normalise one Barsy article row → the cache fields (shared by full sync + single refresh)."""
    from .zayavki import _supplier_catalog
    code = str(a.get("code") or "")
    sups = list((_supplier_catalog()["by_code"].get(code) or {}).keys())
    bc = a.get("barcode")
    attrs = _parse_attributes(a)
    price = _numf(a.get("actual_price"))
    price_s = f"{price:.2f}" if price is not None else ""
    name = a.get("article_name") or ""
    barcode = (bc if isinstance(bc, str) else (bc[0] if isinstance(bc, list) and bc else "")) or ""
    supplier = sups[0] if sups else ""
    return {"code": code, "name": name, "barcode": barcode,
            "category": a.get("category_name") or a.get("category") or "", "supplier": supplier,
            "price": price, "has_image": bool(a.get("picture_url")), "attributes": attrs,
            "search_lc": f"{name} {code} {barcode} {supplier} {price_s}".lower(),
            "attr_lc": "|".join(f"{x['key']}={x['value']}" for x in attrs).lower()}


async def refresh_cache_for_code(code: str) -> bool:
    """Re-fetch ONE article from Barsy and upsert its cache row — the write-through target for the
    Barsy article.updated webhook (keeps the cache fresh for changes made outside the panel)."""
    if not code:
        return False
    from ..db import async_session_maker

    def _fetch():
        rows = barsy.call("Articles_getlist", {"filters": {"code": str(code)}, "length": 3,
                          "extra_properties": {"article_details": 1, "picture_url": 1, "has_attributes": 1}}) or []
        for a in rows:
            if str(a.get("code")) == str(code):
                return a
        return rows[0] if rows else None
    a = await run_in_threadpool(_fetch)
    if not a or a.get("article_id") is None:
        return False
    f = _cache_fields(a)
    async with async_session_maker() as session:
        p = await session.get(ProductCache, a["article_id"]) or ProductCache(article_id=a["article_id"])
        p.code, p.name, p.barcode = f["code"], f["name"], f["barcode"]
        p.category, p.supplier, p.price = f["category"], (f["supplier"] or p.supplier), f["price"]
        p.has_image = f["has_image"]
        p.attributes_json = _json.dumps(f["attributes"], ensure_ascii=False)
        p.search_lc, p.attr_lc = f["search_lc"], f["attr_lc"]
        p.updated_at = _dt.datetime.now(_dt.timezone.utc)
        session.add(p)
        await session.commit()
    return True


@router.get("/facets")
async def facets(user: User = Depends(current_active_user),
                 session: AsyncSession = Depends(get_async_session)):
    """Distinct suppliers + categories for the filter dropdowns (from the cache)."""
    sups = [s for (s,) in (await session.execute(
        select(ProductCache.supplier).distinct())).all() if s]
    cats = [c for (c,) in (await session.execute(
        select(ProductCache.category).distinct())).all() if c]
    # attribute key → sorted distinct values, for the attribute-filter dropdowns
    attrs: dict[str, set] = {}
    for (aj,) in (await session.execute(select(ProductCache.attributes_json))).all():
        for x in _json.loads(aj or "[]"):
            if x.get("key") and x.get("value"):
                attrs.setdefault(x["key"], set()).add(x["value"])
    return {"suppliers": sorted(sups), "categories": sorted(cats),
            "attributes": {k: sorted(v) for k, v in sorted(attrs.items())}}


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
