# -*- coding: utf-8 -*-
"""Каталог — browse + edit products straight from BARSY (the system of record). Approved
products auto-push to Barsy, so the panel requests them here. Price stays read-only (Microinvest-
sourced, never edited here — price policy). Editable: name, description, barcode."""
import asyncio
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import bulkjobs
from ..audit import log_action
from ..core import barsy, barsy_write_live

# returned instead of touching Barsy when the master write-gate is off (pre-launch safety)
_GATED = "Barsy запис е изключен (BARSY_WRITE_LIVE=0) — нищо не е записано в ERP."
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..models import User
from ..services import barsy_bulk as _bulk
from ..upstream import upstream
from ..users import current_active_user, current_owner, require_roles

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


# ------------------------------------------------------------------ product images (proxy) ---
# Barsy's image URLs are HTTP-Basic-auth-walled, so the browser can't <img src> them directly.
# We proxy: resolve the article's image via Files_getlist, fetch it with the service creds, stream
# the bytes back. The frontend points <img> at OUR endpoint. Bytes are cached in-process.
def _barsy_host() -> tuple[str, str, str]:
    base, user, pw = barsy._env()                 # base = https://<host>/endpoints/json
    return base.split("/endpoints/")[0], user, pw


_IMG_CACHE: dict = {}                     # article_id -> (bytes, ctype); THUMBS ONLY, successes only
_IMG_CACHE_MAX = 200                      # bound resident memory (thumbs are ~small)


def _fetch_image(article_id: int, size: str):
    """(bytes, content_type) for an article's first image, or None. Thumbs are cached (bounded,
    successes only — a product that later gains an image isn't stuck 404); full images never cached."""
    if size == "thumb" and article_id in _IMG_CACHE:
        return _IMG_CACHE[article_id]
    files = barsy.call("Files_getlist", {"obj_type": "articles", "obj_id": article_id}) or []
    if not isinstance(files, list) or not files:
        return None
    f = files[0]
    rel = (f.get("thumbnail_url") if size == "thumb" else f.get("url")) or f.get("url")
    if not rel:
        return None
    if size == "thumb":
        rel = rel.replace("width=50", "width=200")   # the default 50px thumb is too small for cards
    host, user, pw = _barsy_host()
    url = host + rel
    # SSRF guard: the resolved URL must still point at the Barsy host (rel comes from Barsy's response)
    if urlsplit(url).hostname != urlsplit(host).hostname:
        return None
    # follow_redirects=False (httpx default) is the SSRF guard: a redirect off the Barsy host must
    # never be chased with the Basic creds. A 3xx/4xx therefore yields no image.
    r = httpx.get(url, auth=(user, pw), follow_redirects=False, timeout=20)
    if r.status_code != 200:
        return None
    result = (r.content, (f.get("type") or "image/png"))
    if size == "thumb":
        if len(_IMG_CACHE) >= _IMG_CACHE_MAX:
            _IMG_CACHE.pop(next(iter(_IMG_CACHE)))    # evict oldest (dict is insertion-ordered)
        _IMG_CACHE[article_id] = result
    return result


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
    m: dict = {}
    for page in range(1, 100):        # paginate — one 500-row page misses stock for large catalogues
        try:
            out = barsy.call("Store_amounts", {"action_type": "values", "page_num": page,
                                               "rows": 500, "filters": {}}) or {}
        except Exception:
            break
        rows = out.get("rows") or []
        for r in rows:
            aid = r.get("article_id")
            if aid is None:
                continue
            amount, minimum = _num(r.get("store_amount")), _num(r.get("min_amount"))
            low = (r.get("below_min") == 1) or (minimum is not None and amount is not None and amount <= minimum)
            m[aid] = {"amount": amount, "min": minimum, "low": bool(low)}
        if len(rows) < 500:           # last page
            break
    return m


# ------------------------------------------------------------------ local product cache ---
# A SQLite mirror of Barsy's catalogue (product_cache) so search/filter is instant and doesn't hit
# the low-tier Barsy per keystroke — and so attribute filters + bulk ops work over a selected set.
import datetime as _dt
import json as _json
from decimal import ROUND_HALF_UP, Decimal

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


def _leaf_from_gid(gid: str) -> str:
    """'gid://shopify/TaxonomyCategory/hg-11-2' → 'hg-11-2' (our taxonomy leaf id). '' if absent."""
    return gid.rsplit("/", 1)[-1] if gid else ""


def _leaf_map() -> dict:
    """{code → Shopify taxonomy leaf id} for every ENRICHED product, built in ONE pipeline-db pass
    so a full sync doesn't open a connection per product. Codes not yet enriched are absent (then
    bulk-rename can't recompose a name for them — reported as skipped, not silently wrong)."""
    out: dict = {}
    try:
        from ..core import db as _pdb
        con = _pdb.connect()
        try:
            for code, rj in con.execute("SELECT code, record_json FROM products"):
                try:
                    gid = ((_json.loads(rj) or {}).get("meta") or {}).get("leaf_gid") or ""
                except Exception:
                    gid = ""
                if gid:
                    out[str(code)] = _leaf_from_gid(gid)
        finally:
            con.close()
    except Exception:
        pass
    return out


def _leaf_for(code: str) -> str:
    """One product's Shopify leaf id from its enriched record (single-refresh path)."""
    try:
        from ..core import db as _pdb
        rec = _pdb.get_record(code) or {}
        return _leaf_from_gid(((rec.get("meta") or {}).get("leaf_gid")) or "")
    except Exception:
        return ""


def _sync_fetch() -> list[dict]:
    """Blocking: pull ALL articles + their attributes/stock/picture from Barsy in ONE bulk call
    (extra_properties), enriching supplier from the local code map as a fallback. Paginated to keep
    each page light on the low-tier server."""
    from ..services.supplier_catalog import supplier_catalog as _supplier_catalog
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
    leaves = _leaf_map()                                 # one pass → {code: shopify leaf id}
    out = []
    for a in rows:
        aid = a.get("article_id")
        if aid is None:
            continue
        code = str(a.get("code") or "")
        st = stock.get(aid, {})
        sups = list((by_code.get(code) or {}).keys())
        bc = a.get("barcode")
        dml = a.get("description_ml")
        out.append({
            "article_id": aid, "code": code, "name": a.get("article_name") or "",
            "shopify_category_id": leaves.get(code, ""),
            "barcode": (bc if isinstance(bc, str) else (bc[0] if isinstance(bc, list) and bc else "")) or "",
            "category": a.get("category_name") or a.get("category") or "",
            "supplier": sups[0] if sups else "",
            "brand": str(a.get("brand") or a.get("vendor") or a.get("producer") or ""),
            "description": a.get("description") or (dml.get("bg_BG") if isinstance(dml, dict) else "") or "",
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
    now = _dt.datetime.now(_dt.UTC)
    for d in data:
        p = existing.get(d["article_id"]) or ProductCache(article_id=d["article_id"])
        p.code, p.name, p.barcode = d["code"], d["name"], d["barcode"]
        p.category, p.supplier = d["category"], d["supplier"]
        p.brand, p.desc_lc = d.get("brand", ""), str(d.get("description", "")).lower()
        p.shopify_category_id = d.get("shopify_category_id", "") or p.shopify_category_id
        p.price, p.stock, p.min_stock, p.low = d["price"], d["stock"], d["min_stock"], d["low"]
        p.has_image = d["has_image"]
        p.attributes_json = _json.dumps(d["attributes"], ensure_ascii=False)
        # universal free-text key — name, code, barcode, supplier, brand AND price (widen the index,
        # incl. price at 2dp so "19.90" or "19.9" both match)
        price_s = f"{d['price']:.2f}" if d["price"] is not None else ""
        p.search_lc = f"{d['name']} {d['code']} {d['barcode']} {d['supplier']} {d.get('brand','')} {price_s}".lower()
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
    brand: str = Query(""), desc: str = Query(""),
    price_min: float | None = Query(None), price_max: float | None = Query(None),
    stock_min: float | None = Query(None), stock_max: float | None = Query(None),
    low_only: bool = Query(False),
    has_image: bool | None = Query(None), has_barcode: bool | None = Query(None),
    has_category: bool | None = Query(None), has_price: bool | None = Query(None),
    attr: list[str] = Query([]),
    sort: str = Query("name"), order: str = Query("asc"),
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
):
    """INSTANT filtered search over the local cache (no Barsy round-trip). Granular: text, supplier,
    category, brand, description-contains, price/stock ranges, low-stock, missing-image/barcode/
    category/price, and repeated attr="Ключ=Стойност" (AND)."""
    stmt = select(ProductCache)
    if q.strip():
        stmt = stmt.where(ProductCache.search_lc.like(f"%{q.strip().lower()}%"))  # Cyrillic-safe
    if supplier:
        stmt = stmt.where(ProductCache.supplier == supplier)
    if category:
        stmt = stmt.where(ProductCache.category == category)
    if brand:
        stmt = stmt.where(ProductCache.brand == brand)
    if desc.strip():
        stmt = stmt.where(ProductCache.desc_lc.like(f"%{desc.strip().lower()}%"))
    if price_min is not None:
        stmt = stmt.where(cast(ProductCache.price, Float) >= price_min)
    if price_max is not None:
        stmt = stmt.where(cast(ProductCache.price, Float) <= price_max)
    if stock_min is not None:
        stmt = stmt.where(ProductCache.stock >= stock_min)
    if stock_max is not None:
        stmt = stmt.where(ProductCache.stock <= stock_max)
    if low_only:
        stmt = stmt.where(ProductCache.low.is_(True))
    if has_image is not None:
        stmt = stmt.where(ProductCache.has_image.is_(has_image))
    if has_barcode is not None:
        stmt = stmt.where(ProductCache.barcode != "" if has_barcode else ProductCache.barcode == "")
    if has_category is not None:
        stmt = stmt.where(ProductCache.category != "" if has_category else ProductCache.category == "")
    if has_price is not None:
        stmt = stmt.where(ProductCache.price.is_not(None) if has_price else ProductCache.price.is_(None))
    for a in attr:                                   # each "key=value" narrows further (AND)
        if a.strip():
            stmt = stmt.where(ProductCache.attr_lc.like(f"%{a.strip().lower()}%"))
    from sqlalchemy import func as _func
    total = (await session.execute(select(_func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = {"name": ProductCache.name, "code": ProductCache.code, "price": ProductCache.price,
           "stock": ProductCache.stock, "supplier": ProductCache.supplier,
           "brand": ProductCache.brand}.get(sort, ProductCache.name)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(stmt)).scalars().all()
    return {"total": total, "page": page, "per_page": per_page, "items": [
        {"article_id": p.article_id, "code": p.code, "name": p.name, "barcode": p.barcode,
         "category": p.category, "supplier": p.supplier, "brand": p.brand, "price": p.price,
         "stock": p.stock, "low": p.low, "has_image": p.has_image,
         "attributes": _json.loads(p.attributes_json or "[]")} for p in rows]}


_BULK_MAX = 2000                 # cap the selection — each apply is a serial, blocking Barsy write
_SYNC_CAP = 30                   # ≤ this runs inline in the request; more → a background job (G4c)


class BulkPriceRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=_BULK_MAX)
    op: str                      # mul | div | add | sub | set
    value: float
    round_to: int = 2
    confirm: bool = False


def _apply_op(old: float | None, op: str, value: float, rd: int) -> float | None:
    """New price as an exact-decimal float, or None if the result is invalid (no baseline for a
    relative op, or a price that would land ≤ 0 — which would silently make the product free)."""
    if op != "set" and old is None:
        return None                                       # relative op with no baseline → refuse
    o = Decimal(str(old if old is not None else 0))
    v = Decimal(str(value))
    n = {"mul": o * v, "div": (o / v if v != 0 else o), "add": o + v,
         "sub": o - v, "set": v}[op]
    n = n.quantize(Decimal(1).scaleb(-rd), rounding=ROUND_HALF_UP)   # ROUND_HALF_UP, not banker's
    if n <= 0:
        return None                                       # never write a free/negative price
    return float(n)


async def _bulk_price_apply(session, changes: list, op: str, value: float, rd: int,
                            base_by_id: dict, job_id: str | None = None):
    """Serial, paced Articles_update over `changes` (write actual_price + write-through the cache).
    Shared by the inline and background paths; reports per-item progress to bulkjobs when job_id set.
    Returns (applied, failed_codes)."""
    ids = [c["article_id"] for c in changes]
    cache = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all()}
    applied, failed = 0, []
    for i, c in enumerate(changes):
        aid = c["article_id"]
        if op == "set":
            new = c["new_price"]
        else:
            base = base_by_id.get(aid)
            if base is None:
                base = cache[aid].price if aid in cache else None
            new = _apply_op(base, op, value, rd)
            if new is None:
                failed.append(c["code"])
                if job_id:
                    bulkjobs.bump(job_id, failed_code=c["code"])
                continue
        if i:
            await _bulk.pace()                            # smooth the write burst (low-tier Barsy throttles)
        try:
            await run_in_threadpool(barsy.call, "Articles_update",
                                    {"article_id": aid, "article": {"actual_price": str(new)}})
        except Exception:
            failed.append(c["code"])
            if job_id:
                bulkjobs.bump(job_id, failed_code=c["code"])
            continue
        if aid in cache:
            cache[aid].price = new                        # write-through to the cache
        c["new_price"] = new
        applied += 1
        if job_id:
            bulkjobs.bump(job_id, applied=1)
    await session.commit()
    return applied, failed


async def _bulk_price_bg(job_id, changes, op, value, rd, base_by_id, actor):
    """Run a large bulk-price apply off the request, in its own DB session, updating the job."""
    from ..db import async_session_maker
    try:
        async with async_session_maker() as session:
            applied, failed = await _bulk_price_apply(session, changes, op, value, rd, base_by_id, job_id)
            await log_action(session, actor, "catalog.bulk_price",
                             detail=f"op={op} value={value} applied={applied} failed={len(failed)} (bg)")
        bulkjobs.finish(job_id)
    except Exception as e:                                # never leave the job stuck 'running'
        bulkjobs.finish(job_id, status="error", note=str(e)[:180])


@router.get("/bulk-jobs/{job_id}")
async def bulk_job(job_id: str, user: User = Depends(current_active_user)):
    """Progress of a background bulk op (G4c): {op,total,done,applied,skipped,failed[],status}."""
    j = bulkjobs.get(job_id)
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава задача.")
    return j


@router.post("/bulk-price")
async def bulk_price(req: BulkPriceRequest, actor: User = Depends(current_owner),
                     session: AsyncSession = Depends(get_async_session)):
    """Bulk change the SELL price (actual_price) of the selected products. Preview first (confirm=
    false → computed new prices, writes nothing); confirm=true writes each via Articles_update and
    updates the cache. Owner/manager only, audited. For relative ops the live Barsy price is re-read
    at apply time so a stale cache can't quietly revert a price. (Permanent base change — the panel is
    source of truth for price post-Microinvest-migration; timed promos use Pricelists separately.)"""
    if req.op not in ("mul", "div", "add", "sub", "set"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна операция.")
    if req.op == "div" and req.value == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Деление на нула.")
    rd = max(0, min(4, req.round_to))
    rows = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(req.article_ids)))).scalars().all()}
    changes, skipped = [], []
    for aid in req.article_ids:
        p = rows.get(aid)
        if not p:
            continue
        new = _apply_op(p.price, req.op, req.value, rd)
        if new is None:
            skipped.append({"article_id": aid, "code": p.code, "name": p.name, "old_price": p.price,
                            "reason": "няма базова цена" if p.price is None else "цената би станала ≤ 0"})
            continue
        changes.append({"article_id": aid, "code": p.code, "name": p.name,
                        "old_price": p.price, "new_price": new})
    if not req.confirm:
        return {"dry_run": True, "count": len(changes), "changes": changes, "skipped": skipped}
    if not barsy_write_live():
        return {"dry_run": True, "gated": True, "note": _GATED, "applied": 0, "failed": [],
                "count": len(changes), "changes": changes, "skipped": skipped}

    # relative ops need the live baseline (a stale cache value could revert a real change). Fetch all
    # baselines in a few Articles_getlist pages instead of one Articles_get per item (was a 2N N+1).
    live_base: dict = {}
    if req.op != "set":
        live_base = await run_in_threadpool(_bulk.price_baselines, [c["article_id"] for c in changes])
    # G4c: a big batch is a minutes-long paced write chain — past an HTTP request's patience. Above the
    # cap, run it as a background job and return a job_id the client polls (GET /bulk-jobs/{id}).
    if len(changes) > _SYNC_CAP:
        job_id = bulkjobs.create("bulk_price", len(changes))
        actor_lite = SimpleNamespace(id=actor.id, email=actor.email)
        asyncio.create_task(_bulk_price_bg(job_id, changes, req.op, req.value, rd, live_base, actor_lite))
        return {"async": True, "job_id": job_id, "count": len(changes),
                "changes": changes, "skipped": skipped}
    applied, failed = await _bulk_price_apply(session, changes, req.op, req.value, rd, live_base)
    await log_action(session, actor, "catalog.bulk_price",
                     detail=f"op={req.op} value={req.value} applied={applied} failed={len(failed)}")
    return {"dry_run": False, "count": len(changes), "applied": applied,
            "failed": failed, "skipped": skipped}


class PromoRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=_BULK_MAX)
    name: str
    mode: str = "discount_pct"          # discount_pct | discount_eur | exact
    value: float
    date_from: str                       # YYYY-MM-DD
    date_to: str
    confirm: bool = False


_PROMO_MOD = {"discount_pct": 2, "discount_eur": 4, "exact": 5}   # Barsy modificator_type


def _period(date_from: str, date_to: str) -> str:
    """YYYY-MM-DD range → Barsy periods_date "d.m-d.m". from>to is allowed (a promo crossing
    year-end). Raises ValueError on anything that isn't a real calendar date."""
    def dm(s: str) -> str:
        try:
            d = _dt.datetime.strptime((s or "").strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            raise ValueError("дата трябва да е ГГГГ-ММ-ДД")
        return f"{d.day}.{d.month}"
    return f"{dm(date_from)}-{dm(date_to)}"


@router.post("/promo")
async def create_promo(req: PromoRequest, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    """Create a NATIVE Barsy timed promotion (Pricelists rule) on the selected products — a % or €
    discount, or an exact promo price, active over a date window. Barsy adjusts current_price for the
    window and auto-reverts; the base actual_price is untouched. Owner-only, audited."""
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
    if not barsy_write_live():
        return {"dry_run": True, "gated": True, "note": _GATED, "count": len(req.article_ids),
                "period": period, "name": req.name, "mode": req.mode, "value": req.value}

    values = {
        "pricelist_name": req.name.strip(), "pricelist_type_id": 6, "pricelist_group_id": 1,
        "weight": 1, "periods_date": period, "begin_price_type": 1, "price_tax_mode": 1,
        "price_tax_id": 2, "modificator_type": _PROMO_MOD[req.mode], "modificator_value": req.value,
        "modificator_round_type": 0, "price_change_groups": [1], "total_period": "current",
        "price_type": 1,
    }
    # 1) create the rule, then resolve its id. Pricelists_edit(save) doesn't return the new id on this
    #    install, so we snapshot the existing pricelist ids BEFORE the save and pick the row that
    #    appeared AFTER — unambiguous even if a promo with the same name already exists (a plain
    #    name-scan can't tell an old same-named promo from the new one). active_struct_id is a string
    #    here though edit.rst types it int — install-specific, verified in the live pilot.
    def _pricelist_rows():
        idx = barsy.call("Pricelists_index", {"action_type": "values"}) or {}
        return (idx.get("rows", idx) if isinstance(idx, dict) else idx) or []
    try:
        before_ids = {r.get("pricelist_id") for r in await run_in_threadpool(_pricelist_rows)}
        await run_in_threadpool(barsy.call, "Pricelists_edit",
                                {"action_type": "save", "active_struct_id": "eStructForm_1", "values": values})
        pid = None
        for r in await run_in_threadpool(_pricelist_rows):
            rid = r.get("pricelist_id")
            if rid is None or rid in before_ids:      # only rows that did NOT exist before the save
                continue
            if str(r.get("pricelist_name")) == req.name.strip():
                pid = rid if pid is None else max(pid, rid)
        if pid is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Промоцията е създадена, но ID не се разчете — провери в Barsy.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy (промоция): {str(e)[:180]}")
    # 2) attach the articles — if this fails, delete the just-created empty rule so we don't orphan it
    attach_rows = [{"article_id": aid, "req_amount": 1, "price_change_group": 1} for aid in req.article_ids]
    try:
        await run_in_threadpool(barsy.call, "Pricelists_save_articles", {"id": pid, "rows": attach_rows})
    except Exception as e:
        try:
            await run_in_threadpool(barsy.call, "Pricelists_delete", {"id": pid})
        except Exception:
            pass
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Barsy (прикачване): {str(e)[:160]} — празната промоция е премахната.")
    await log_action(session, actor, "catalog.promo", target=req.name.strip(),
                     detail=f"pricelist={pid} mode={req.mode} value={req.value} period={period} n={len(req.article_ids)}")
    return {"dry_run": False, "pricelist_id": pid, "count": len(req.article_ids), "period": period}


def _cache_fields(a: dict) -> dict:
    """Normalise one Barsy article row → the cache fields (shared by full sync + single refresh)."""
    from ..services.supplier_catalog import supplier_catalog as _supplier_catalog
    code = str(a.get("code") or "")
    sups = list((_supplier_catalog()["by_code"].get(code) or {}).keys())
    bc = a.get("barcode")
    attrs = _parse_attributes(a)
    price = _numf(a.get("actual_price"))
    price_s = f"{price:.2f}" if price is not None else ""
    name = a.get("article_name") or ""
    barcode = (bc if isinstance(bc, str) else (bc[0] if isinstance(bc, list) and bc else "")) or ""
    supplier = sups[0] if sups else ""
    brand = str(a.get("brand") or a.get("vendor") or a.get("producer") or "")
    dml = a.get("description_ml")
    description = a.get("description") or (dml.get("bg_BG") if isinstance(dml, dict) else "") or ""
    return {"code": code, "name": name, "barcode": barcode,
            "category": a.get("category_name") or a.get("category") or "", "supplier": supplier,
            "shopify_category_id": _leaf_for(code),
            "brand": brand, "desc_lc": str(description).lower(),
            "price": price, "has_image": bool(a.get("picture_url")), "attributes": attrs,
            "search_lc": f"{name} {code} {barcode} {supplier} {brand} {price_s}".lower(),
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
        p.brand, p.desc_lc = f["brand"], f["desc_lc"]
        p.shopify_category_id = f.get("shopify_category_id", "") or p.shopify_category_id
        p.has_image = f["has_image"]
        p.attributes_json = _json.dumps(f["attributes"], ensure_ascii=False)
        p.search_lc, p.attr_lc = f["search_lc"], f["attr_lc"]
        p.updated_at = _dt.datetime.now(_dt.UTC)
        session.add(p)
        await session.commit()
    return True


@router.get("/promos")
async def list_promos(actor: User = Depends(current_owner)):
    """Active Barsy price rules (Pricelists) — so staff can see + archive running promotions."""
    idx = await run_in_threadpool(barsy.call, "Pricelists_index", {"action_type": "values"}) or {}
    rows = idx.get("rows", idx) if isinstance(idx, dict) else idx
    out = []
    for r in (rows or []):
        pid = r.get("pricelist_id")
        if pid is None:
            continue
        out.append({"id": pid, "name": r.get("pricelist_name") or "",
                    "period": r.get("periods_date") or r.get("period") or "",
                    "active": not r.get("delete_flag")})
    return {"promos": out}


@router.post("/promos/{pid}/delete")
async def delete_promo(pid: int, actor: User = Depends(current_owner),
                       session: AsyncSession = Depends(get_async_session)):
    """Remove a promo rule (Pricelists_delete). Base prices are untouched (promos never wrote them)."""
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    try:
        await run_in_threadpool(barsy.call, "Pricelists_delete", {"id": pid})
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {str(e)[:160]}")
    await log_action(session, actor, "catalog.promo_delete", target=str(pid))
    return {"ok": True}


class BulkRenameRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=_BULK_MAX)
    style: dict[str, str] = {}          # optional recipe override for this run (else per-leaf/global)
    confirm: bool = False


async def _bulk_rename_apply(session, changes: list, job_id: str | None = None):
    """Serial, paced Articles_update renaming `changes` (write-through the cache). Shared by the inline
    and background paths; reports progress to bulkjobs when job_id set. Returns (applied, failed_codes)."""
    ids = [c["article_id"] for c in changes]
    cache = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all()}
    applied, failed = 0, []
    for i, c in enumerate(changes):
        if i:
            await _bulk.pace()
        try:
            await run_in_threadpool(barsy.call, "Articles_update", {"article_id": c["article_id"],
                "article": {"article_name": c["new_name"], "article_name_ml": {"bg_BG": c["new_name"]}}})
        except Exception:
            failed.append(c["code"])
            if job_id:
                bulkjobs.bump(job_id, failed_code=c["code"])
            continue
        if c["article_id"] in cache:
            cache[c["article_id"]].name = c["new_name"]      # write-through
        applied += 1
        if job_id:
            bulkjobs.bump(job_id, applied=1)
    await session.commit()
    return applied, failed


async def _bulk_rename_bg(job_id, changes, actor):
    from ..db import async_session_maker
    try:
        async with async_session_maker() as session:
            applied, failed = await _bulk_rename_apply(session, changes, job_id)
            await log_action(session, actor, "catalog.bulk_rename",
                             detail=f"applied={applied} failed={len(failed)} (bg)")
        bulkjobs.finish(job_id)
    except Exception as e:
        bulkjobs.finish(job_id, status="error", note=str(e)[:180])


@router.post("/bulk-rename")
async def bulk_rename(req: BulkRenameRequest, actor: User = Depends(current_owner),
                      session: AsyncSession = Depends(get_async_session)):
    """Recompose the NAME of the selected products via the v3 naming recipe (leaf + attributes + brand),
    optionally under a chosen style override. Preview first (confirm=false); confirm=true writes each
    via Articles_update. Products with no resolvable Shopify leaf are reported as `skipped`."""
    from .. import naming
    from . import intake as I
    cats = {c["id"]: c for c in I._tax_categories()}
    rows = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(req.article_ids)))).scalars().all()}
    override = {k: v for k, v in req.style.items() if v}
    changes, skipped = [], []
    for aid in req.article_ids:
        p = rows.get(aid)
        if not p:
            continue
        leaf = getattr(p, "shopify_category_id", "") or ""
        cat = cats.get(leaf)
        if not cat:
            skipped.append({"article_id": aid, "code": p.code, "name": p.name, "reason": "няма Shopify категория"})
            continue
        attrs = {a["key"]: a["value"] for a in _json.loads(p.attributes_json or "[]") if a.get("key")}
        facts = I._facts_from(cat.get("bg") or "", p.brand, attrs)
        style = {**(await I._recipe_style(session, leaf)), **override}
        new = naming.build(facts, style or None)
        if not new or new == p.name:
            skipped.append({"article_id": aid, "code": p.code, "name": p.name,
                            "reason": "без промяна" if new == p.name else "не може да се състави име"})
            continue
        changes.append({"article_id": aid, "code": p.code, "old_name": p.name, "new_name": new})
    if not req.confirm:
        return {"dry_run": True, "count": len(changes), "changes": changes, "skipped": skipped}
    if not barsy_write_live():
        return {"dry_run": True, "gated": True, "note": _GATED, "applied": 0, "failed": [],
                "count": len(changes), "changes": changes, "skipped": skipped}
    if len(changes) > _SYNC_CAP:                              # G4c: large rename → background job
        job_id = bulkjobs.create("bulk_rename", len(changes))
        actor_lite = SimpleNamespace(id=actor.id, email=actor.email)
        asyncio.create_task(_bulk_rename_bg(job_id, changes, actor_lite))
        return {"async": True, "job_id": job_id, "count": len(changes),
                "changes": changes, "skipped": skipped}
    applied, failed = await _bulk_rename_apply(session, changes)
    await log_action(session, actor, "catalog.bulk_rename",
                     detail=f"applied={applied} failed={len(failed)} skipped={len(skipped)}")
    return {"dry_run": False, "count": len(changes), "applied": applied, "failed": failed, "skipped": skipped}


@router.get("/facets")
async def facets(user: User = Depends(current_active_user),
                 session: AsyncSession = Depends(get_async_session)):
    """Distinct suppliers + categories + brands for the filter dropdowns (from the cache)."""
    sups = [s for (s,) in (await session.execute(
        select(ProductCache.supplier).distinct())).all() if s]
    cats = [c for (c,) in (await session.execute(
        select(ProductCache.category).distinct())).all() if c]
    brands = [b for (b,) in (await session.execute(
        select(ProductCache.brand).distinct())).all() if b]
    # attribute key → sorted distinct values, for the attribute-filter dropdowns
    attrs: dict[str, set] = {}
    for (aj,) in (await session.execute(select(ProductCache.attributes_json))).all():
        for x in _json.loads(aj or "[]"):
            if x.get("key") and x.get("value"):
                attrs.setdefault(x["key"], set()).add(x["value"])
    return {"suppliers": sorted(sups), "categories": sorted(cats), "brands": sorted(brands),
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
        # extra_data (SEO + weight_kg) is omitted by a default get — request it explicitly
        a = barsy.call("Articles_get", {"article_id": article_id, "extra_properties": {"extra_data": 1}})
    if not isinstance(a, dict) or not a.get("article_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Артикулът не е намерен.")
    bc = a.get("barcode")
    if isinstance(bc, list):
        bc = bc[0] if bc else ""
    _ed = a.get("extra_data")
    extra = _ed if isinstance(_ed, dict) else {}
    return {
        "article_id": a.get("article_id"), "code": a.get("code") or "",
        "name": a.get("article_name") or "", "description": a.get("description") or "",
        "price": a.get("actual_price"), "barcode": bc or "",
        "unit": a.get("amount_type_name_short") or "",
        "category": a.get("master_cat") or (a.get("categories_names") or ""),
        "oversell": str(a.get("sale_below_zero") or "").upper() == "YES",
        "weight_kg": str(extra.get("weight_kg") or ""),
    }


class ArticleEdit(BaseModel):
    name: str
    description: str = ""
    barcode: str = ""
    oversell: bool = False        # orderable at <=0 stock (backorder / supplier dropship)
    weight_kg: str = ""           # kg → merged into Barsy extra_data (preserving SEO)


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
    # explicit YES/NO (not the DEF default) so the toggle can turn oversell back OFF on an edit
    article["sale_below_zero"] = "YES" if req.oversell else "NO"
    # weight rides in the native extra_data object — MERGE, don't clobber (it also holds SEO). Read the
    # current extra_data first so seo_title/seo_description/variant_codes survive the write.
    w = req.weight_kg.strip()
    if w:
        try:
            cur = await run_in_threadpool(
                barsy.call, "Articles_get", {"article_id": article_id, "extra_properties": {"extra_data": 1}})
            extra = dict(cur.get("extra_data") or {}) if isinstance(cur, dict) else {}
        except Exception:
            extra = {}
        extra["weight_kg"] = w
        article["extra_data"] = extra
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    with upstream("Barsy обновяване"):
        await run_in_threadpool(barsy.call, "Articles_update",
                                {"article_id": article_id, "article": article})
    await log_action(session, actor, "article.update", target=str(article_id),
                     detail=f"name={req.name[:40]}")
    return {"ok": True}
