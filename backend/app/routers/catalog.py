# -*- coding: utf-8 -*-
"""Каталог — browse + edit products straight from BARSY (the system of record). Approved
products auto-push to Barsy, so the panel requests them here. Price stays read-only (Microinvest-
sourced, never edited here — price policy). Everything the create path (intake) writes is editable
here too: name(+public), description(+public), barcode, brand/attributes, category, unit, SEO,
weight, oversell and the sale/publish status — plus the accounting/stock-behaviour block (ДДС група,
партиди, разфасовка, min/max количество, отстъпка/печалба, цена втора валута, клиентска заявка).
A Barsy field with no control here is unmanageable and invisible to staff, so read-back and write
must stay in step with the live article form."""
import asyncio
import datetime
import re
import time
import typing
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
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..models import CategoryVisibility, User
from ..services import barsy_bulk as _bulk
from ..upstream import upstream
from ..users import current_active_user, current_owner, require_roles

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

PANEL_LOCALE = "bg_BG"    # panel UI language. Display names are read from article_name_ml[PANEL_LOCALE]
# with fallback to the scalar article_name. Groundwork for the English site: switch this (or thread a
# per-request locale) to render another language. article_name and ml.bg_BG are kept in sync by every
# write path, so this is a no-op for bg today -- it just makes the ml the source of truth for display.


def _name_for_locale(a: dict, locale: str = PANEL_LOCALE) -> str:
    ml = a.get("article_name_ml")
    if isinstance(ml, dict) and isinstance(ml.get(locale), str) and ml[locale].strip():
        return ml[locale]
    return a.get("article_name") or ""


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
import logging
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Float, cast, select
from sqlalchemy import delete as _sa_delete

from ..models import CatalogSyncState, ProductCache

log = logging.getLogger("darling.catalog")


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
            "article_id": aid, "code": code, "name": _name_for_locale(a),
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


_PRUNE_MIN_FRACTION = 0.8    # a fetch smaller than this share of the cache is SHORT -> never prune
_PRUNE_CHUNK = 500           # SQLite caps bound parameters per statement; delete in chunks


async def _sync_state(session: AsyncSession) -> CatalogSyncState:
    """The single catalog_sync_state row, created on first use. Caller commits."""
    st = await session.get(CatalogSyncState, 1)
    if st is None:
        st = CatalogSyncState(id=1)
        session.add(st)
    return st


async def record_cache_event(kind: str) -> None:
    """Stamp a webhook-driven cache write (kind: updated | deleted). Between full reconciliations
    this is the ONLY evidence the mirror is still being fed: a dead webhook subscription looks
    exactly like a quiet day unless someone can see this timestamp go stale."""
    from ..db import async_session_maker
    async with async_session_maker() as session:
        st = await _sync_state(session)
        st.last_event_at = _dt.datetime.now(_dt.UTC)
        st.last_event_kind = kind
        await session.commit()


@router.post("/sync")
async def sync_cache(actor: User = Depends(require_roles("staff")),
                     session: AsyncSession = Depends(get_async_session)):
    """Full RECONCILIATION of the local cache against Barsy: one big Articles_getlist + one
    Store_amounts, upsert everything Barsy returned, then PRUNE the rows Barsy no longer has.
    Webhooks keep the mirror live in real time; this is the safety net for events we missed.

    The prune is the destructive half, so it is fail-CLOSED: it runs only when the fetch clearly
    SUCCEEDED and looks complete - Barsy returned more than zero articles AND at least
    _PRUNE_MIN_FRACTION of what we already hold. A throttled, short-paginated or half-failed read
    returns fewer articles than the catalogue really has, and pruning on that would delete products
    that exist. Upserting is non-destructive, so a short fetch still refreshes what it did return;
    only the delete is skipped, loudly, and last_sync is NOT advanced (the mirror is still stale)."""
    from sqlalchemy import func as _func
    now = _dt.datetime.now(_dt.UTC)
    try:
        data = await run_in_threadpool(_sync_fetch)
    except Exception as e:
        # A failed read tells us nothing about what Barsy holds -> touch nothing at all.
        st = await _sync_state(session)
        st.last_attempt, st.last_status = now, "error"
        await session.commit()
        log.exception("catalog sync: Barsy fetch failed; cache left untouched: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {str(e)[:160]}")
    existing = {p.article_id: p for p in (await session.execute(select(ProductCache))).scalars().all()}
    for d in data:
        p = existing.get(d["article_id"]) or ProductCache(article_id=d["article_id"])
        p.code, p.name, p.barcode = d["code"], d["name"], d["barcode"]
        p.category, p.supplier = d["category"], d["supplier"]
        p.brand, p.desc_lc = d.get("brand", ""), str(d.get("description", "")).lower()
        p.shopify_category_id = d.get("shopify_category_id", "") or p.shopify_category_id
        p.price, p.stock, p.min_stock, p.low = d["price"], d["stock"], d["min_stock"], d["low"]
        p.has_image = d["has_image"]
        p.attributes_json = _json.dumps(d["attributes"], ensure_ascii=False)
        # universal free-text key - name, code, barcode, supplier, brand AND price (widen the index,
        # incl. price at 2dp so "19.90" or "19.9" both match)
        price_s = f"{d['price']:.2f}" if d["price"] is not None else ""
        p.search_lc = f"{d['name']} {d['code']} {d['barcode']} {d['supplier']} {d.get('brand','')} {price_s}".lower()
        p.attr_lc = "|".join(f"{x['key']}={x['value']}" for x in d["attributes"]).lower()
        p.updated_at = now
        session.add(p)
    # --- prune: rows Barsy no longer returns, i.e. articles deleted in the ERP ---
    fetched, cached = len(data), len(existing)
    complete = fetched > 0 and fetched >= cached * _PRUNE_MIN_FRACTION
    seen = {d["article_id"] for d in data}
    gone = [aid for aid in existing if aid not in seen]
    pruned = 0
    if complete and gone:
        for k in range(0, len(gone), _PRUNE_CHUNK):
            await session.execute(_sa_delete(ProductCache).where(
                ProductCache.article_id.in_(gone[k:k + _PRUNE_CHUNK])))
        pruned = len(gone)
    st = await _sync_state(session)
    st.last_attempt, st.live_count, st.pruned = now, fetched, pruned
    st.last_status = "ok" if complete else "short_fetch"
    if complete:
        st.last_sync = now          # only a complete, trusted pass counts as a real sync
    await session.commit()
    if not complete:
        log.warning("catalog sync: Barsy returned %d articles for a cache of %d - REFUSED to prune "
                    "%d row(s). A short fetch must never wipe the catalogue.", fetched, cached, len(gone))
    await log_action(session, actor, "catalog.sync",
                     detail=f"cached={fetched} pruned={pruned} status={st.last_status}")
    total = (await session.execute(select(_func.count()).select_from(ProductCache))).scalar() or 0
    out: dict[str, object] = {"synced": fetched, "pruned": pruned,
                              "prune_refused": not complete, "cached_count": total}
    if not complete:
        out["note"] = "Синхронизацията е непълна — Barsy върна по-малко артикули от кеша; нищо не е изтрито."
    return out


@router.get("/cache-status")
async def cache_status(user: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    """Freshness of the local mirror, for the catalogue tab's staleness line. Deliberately cheap and
    Barsy-free - the UI polls this, and polling must never reach the ERP. `live_count` is what Barsy
    returned at the LAST reconciliation, not a live count. Three different clocks, on purpose:
    `last_sync` = last complete reconciliation, `last_event_at` = last webhook write-through,
    `last_change` = newest row touch from any source."""
    from sqlalchemy import func as _func
    total = (await session.execute(select(_func.count()).select_from(ProductCache))).scalar() or 0
    last_change = (await session.execute(select(_func.max(ProductCache.updated_at)))).scalar()
    st = await session.get(CatalogSyncState, 1)

    def _iso(v):
        return v.isoformat() if v else None
    return {
        "count": total,                       # `count` is the key the existing UI reads - kept
        "cached_count": total,
        "live_count": st.live_count if st else None,
        "last_sync": _iso(st.last_sync) if st else None,
        "last_attempt": _iso(st.last_attempt) if st else None,
        "last_status": (st.last_status if st else "") or "",
        "pruned": st.pruned if st else 0,
        "last_change": _iso(last_change),
        "last_event_at": _iso(st.last_event_at) if st else None,
        "last_event_kind": (st.last_event_kind if st else "") or "",
    }


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
    """Persist the new prices in one native Barsy save_mass (chunked+paced) instead of a per-article
    Articles_update loop -- Barsy owns the write as a transaction, the way its own grid does. Reports
    progress to bulkjobs when job_id set. Returns (applied, failed_codes)."""
    ids = [c["article_id"] for c in changes]
    cache = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all()}
    to_write, failed = [], []
    for c in changes:
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
        c["new_price"] = new
        to_write.append({"article_id": aid, "col_name": "actual_price", "value": str(new)})
    applied = 0
    if to_write:
        applied = await run_in_threadpool(_bulk.save_mass, to_write)
        written = {w["article_id"] for w in to_write}
        for c in changes:
            if c["article_id"] in written and c["article_id"] in cache:
                cache[c["article_id"]].price = c["new_price"]   # write-through to the cache
        if job_id:
            bulkjobs.bump(job_id, applied=applied, count=applied)
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


async def sync_cache_for_code(code: str) -> str:
    """Bring ONE article's cache row in step with Barsy. Returns THREE states, never a boolean:
      'updated' - Barsy returned the article; its row was upserted.
      'missing' - Barsy answered cleanly and has no such code; the article is gone from the ERP.
      'error'   - we could not ask Barsy, or it answered something we cannot read.
    The distinction is load-bearing: ONLY 'missing' may delete a cache row. Collapsing a failed ERP
    read into the same falsy value as "the article is gone" would let one bad connection empty the
    whole catalogue."""
    if not code:
        return "error"
    from ..db import async_session_maker

    def _fetch():
        rows = barsy.call("Articles_getlist", {"filters": {"code": str(code)}, "length": 3,
                          "extra_properties": {"article_details": 1, "picture_url": 1, "has_attributes": 1}})
        if not isinstance(rows, list):
            # An unreadable answer is NOT proof of absence, and errors must never delete.
            raise TypeError(f"Articles_getlist returned {type(rows).__name__}, expected list")
        return rows
    try:
        rows = await run_in_threadpool(_fetch)
    except Exception as e:
        log.warning("catalog: Barsy read for %s failed (%s) - cache row left untouched.", code, e)
        return "error"
    if not rows:
        return "missing"                       # clean answer, no such article -> it is gone
    # Barsy's code filter can match more than the exact code, so prefer the exact row; falling back
    # to the first row keeps the old write-through behaviour and, importantly, never deletes.
    a = next((r for r in rows if str(r.get("code")) == str(code)), rows[0])
    if a.get("article_id") is None:
        return "error"
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
    return "updated"


async def refresh_cache_for_code(code: str) -> bool:
    """Back-compat wrapper (True only when the row was refreshed). Prefer sync_cache_for_code: this
    boolean cannot tell "the article is gone" from "Barsy did not answer", and that difference
    decides whether a row may be deleted."""
    return (await sync_cache_for_code(code)) == "updated"


async def delete_cache_for_code(code: str) -> int:
    """Drop the cache row(s) for an article that no longer exists in Barsy; returns rows removed.
    Idempotent - a repeat delivery removes nothing and is not an error. Callers must have proof of
    absence from Barsy first (sync_cache_for_code == 'missing'); this function does not check."""
    if not code:
        return 0
    from ..db import async_session_maker
    async with async_session_maker() as session:
        res = typing.cast(CursorResult, await session.execute(
            _sa_delete(ProductCache).where(ProductCache.code == str(code))))
        await session.commit()
        return int(res.rowcount or 0)


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


def _merge_name_ml(current_ml, name, locale="bg_BG"):
    """Merge `name` into `locale` of an existing article_name_ml, PRESERVING other locales. Barsy
    treats a partial {locale: name} on Articles_update as a FULL REPLACE (verified: drops en_US), so
    a rename must send the whole merged map. Null locales Barsy returns are dropped."""
    ml = {k: v for k, v in (current_ml or {}).items() if v}
    ml[locale] = name
    return ml


async def _bulk_rename_apply(session, changes: list, job_id: str | None = None):
    """Serial, paced rename via Articles_update, i18n-safe: for each article read the current
    article_name_ml, merge the new name into the active locale (preserving en_US and any other
    locales), and write article_name + the FULL merged map. Kept on Articles_update (NOT the native
    grid save_mass): the grid writes only the scalar article_name and cannot maintain the ml (verified
    4 ways + live Barsy UI capture), which the panel will read for the English site. Write-through the
    cache. Returns (applied, failed_codes)."""
    ids = [c["article_id"] for c in changes]
    cache = {p.article_id: p for p in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all()}
    applied, failed = 0, []
    for i, c in enumerate(changes):
        aid = c["article_id"]
        if i:
            await _bulk.pace()
        try:
            cur = await run_in_threadpool(barsy.call, "Articles_get", {"article_id": aid})
            ml = _merge_name_ml((cur or {}).get("article_name_ml"), c["new_name"])
            await run_in_threadpool(barsy.call, "Articles_update", {"article_id": aid,
                "article": {"article_name": c["new_name"], "article_name_ml": ml}})
        except Exception:
            failed.append(c["code"])
            if job_id:
                bulkjobs.bump(job_id, failed_code=c["code"])
            continue
        if aid in cache:
            cache[aid].name = c["new_name"]      # write-through
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
                      "name": _name_for_locale(a), "price": a.get("actual_price"),
                      "stock": st.get("amount"), "min_stock": st.get("min"), "low": bool(st.get("low")),
                      "has_image": bool(a.get("picture_url"))})
    return {"items": items}


_BRAND_DETAIL = "Марка"           # brand has no native Barsy field — it's an article detail row


def _read_details(a: dict) -> list[dict]:
    """Article details from a Barsy read → [{key, value}]. Barsy hands them back under `details` or
    `article_details`, as a list or an {idx: row} map, named key/value or detail_name/detail_value —
    normalise all four shapes so the editor round-trips whatever this install returns."""
    raw = a.get("details") or a.get("article_details") or []
    rows = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        k = str(r.get("key") or r.get("detail_name") or "").strip()
        if k:
            out.append({"key": k, "value": str(r.get("value") or r.get("detail_value") or "")})
    return out


def _aliases(raw) -> list[str]:
    """article_aliases → flat list[str] (list | {idx: row} | bare string). READ-ONLY in the editor:
    Barsy accepts aliases on create only, so we show them but never write them back."""
    rows = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else ([raw] if raw else []))
    out = []
    for r in rows:
        s = str((r.get("alias") or r.get("name") or "") if isinstance(r, dict) else r).strip()
        if s:
            out.append(s)
    return out


def _flag(v) -> bool:
    """Barsy booleans arrive as 1/0, "1"/"0" or YES/NO depending on the field — normalise to bool."""
    if isinstance(v, str):
        return v.strip().upper() in ("1", "YES", "TRUE")
    return bool(v)


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pkg_or_none(v):
    """default_package_id: Barsy sends "" (and sometimes 0) for «без разфасовка» — both mean
    "no package", so they must read back as null, not as a package with id 0."""
    return _int_or_none(v) or None


_TRI = ("DEF", "YES", "NO")   # Според правата / може без наличност / не може


def _tri(v) -> str:
    """sale_below_zero + storemove_below_zero are TRI-state in Barsy: DEF ("Според правата" — fall
    back to the user's rights), YES, NO. The panel used to know only YES/NO, which made DEF
    unreachable and silently overwrote it on every edit. Anything absent/unrecognised reads as DEF:
    we must never invent a hard YES/NO the ERP never stored."""
    s = str(v or "").strip().upper()
    return s if s in _TRI else "DEF"


@router.get("/{article_id:int}")
def get_article(article_id: int, user: User = Depends(current_active_user)):
    """Full product detail from Barsy — every field the editor can write back (create/edit parity)."""
    with upstream("Barsy артикул"):
        # extra_data (SEO + weight_kg) and article_details (brand + attributes) are omitted by a
        # default get — request them explicitly
        a = barsy.call("Articles_get", {"article_id": article_id,
                                        "extra_properties": {"extra_data": 1, "article_details": 1}})
    if not isinstance(a, dict) or not a.get("article_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Артикулът не е намерен.")
    bc = a.get("barcode")
    if isinstance(bc, list):
        bc = bc[0] if bc else ""
    _ed = a.get("extra_data")
    extra = _ed if isinstance(_ed, dict) else {}
    _dp = a.get("description_public_ml")
    details = _read_details(a)
    brand = next((d["value"] for d in details if d["key"] == _BRAND_DETAIL), "")
    for_sale, public = _flag(a.get("is_for_sale")), _flag(a.get("is_public"))
    # Three states again: True / False / None ("could not check"). The editor disables the
    # «Активен» option on False and says why, rather than letting someone pick it and meet a
    # server error — but the server refuses regardless, because a UI rule is not a rule.
    _pic = _picture_url(str(a.get("code") or ""), a.get("article_id"))
    has_image = None if _pic is None else bool(_pic)
    return {
        "article_id": a.get("article_id"), "code": a.get("code") or "",
        "name": _name_for_locale(a), "description": a.get("description") or "",
        "price": a.get("actual_price"), "barcode": bc or "",
        "unit": a.get("amount_type_name_short") or "",
        "category": a.get("master_cat") or (a.get("categories_names") or ""),
        "oversell": str(a.get("sale_below_zero") or "").upper() == "YES",
        "weight_kg": str(extra.get("weight_kg") or ""),
        # the storefront title the connector PREFERS over article_name — until now unwritable here
        "name_public": a.get("article_name_public") or "",
        "description_public": (_dp.get("bg_BG") if isinstance(_dp, dict) else "") or "",
        "brand": brand,
        "attributes": [d for d in details if d["key"] != _BRAND_DETAIL],
        "cat_id": _int_or_none(a.get("master_cat_id") or a.get("cat_id")),
        "amount_type_id": _int_or_none(a.get("amount_type_id")),
        "seo_title": str(extra.get("seo_title") or ""),
        "seo_description": str(extra.get("seo_description") or ""),
        "is_for_sale": for_sale, "is_public": public, "has_image": has_image,
        # drives the Shopify auto-sync (ACTIVE / Draft / Archive)
        "status": "active" if for_sale and public else ("draft" if for_sale else "withdrawn"),
        "ref_num": str(a.get("ref_num") or ""),          # auto from the delivery — read-only
        "aliases": _aliases(a.get("article_aliases")),   # create-only in Barsy — read-only
        # --- accounting / stock-behaviour fields (unmanageable from the panel until now) ---
        "tax_id": _int_or_none(a.get("tax_id")),                     # ДДС група (2=20% Б, 4=9% Г, …)
        "lot_type_id": _int_or_none(a.get("lot_type_id")),           # партиди: Без / Авт. / Ръчно / …
        "default_package_id": _pkg_or_none(a.get("default_package_id")),
        # tri-state (see _tri) — `oversell` above stays for backwards compatibility
        "oversell_mode": _tri(a.get("sale_below_zero")),
        "storemove_mode": _tri(a.get("storemove_below_zero")),
        "max_discount": _numf(a.get("max_discount")),
        "min_profit_percent": _numf(a.get("min_profit_percent")),
        "avg_delivery_price": _numf(a.get("avg_delivery_price")),
        "part_factor": _numf(a.get("part_factor")),
        "min_amount": _numf(a.get("min_amount")),
        "max_amount": _numf(a.get("max_amount")),
        "is_for_client_order": _flag(a.get("is_for_client_order")),
        # `actuial_price_sec_currency` is Barsy's OWN typo for the secondary-currency price. Reading
        # (or writing) the "corrected" spelling silently loses the value — keep it verbatim.
        "price_sec_currency": _numf(a.get("actuial_price_sec_currency")),
        # READ-ONLY display: Комплектация is parked, so recipe semantics stay unverified → never written
        "composite_type_name": str(a.get("composite_type_name") or ""),
        "recipe_description": str(a.get("recipe_description") or ""),
    }


class AttrIn(BaseModel):
    key: str
    value: str = ""


# status → (is_for_sale, is_public). DRAFT = sellable at the till but hidden online.
_STATUS_FLAGS = {"active": (1, 1), "draft": (1, 0), "withdrawn": (0, 0)}

# A product may not go on the storefront without a photo (owner rule, 2026-08-06). A listing
# with no picture does not sell and reads as a broken shop, so the flag that puts it online
# is gated on the ERP actually having one.
_NO_IMAGE = ("Продуктът няма снимка. Без поне една снимка не може да се пусне в сайта — "
             "качи снимка в Barsy и опитай пак.")
_IMAGE_UNKNOWN = ("Не можах да проверя дали продуктът има снимка (Barsy не отговори). "
                  "Не го пускам в сайта на сляпо — опитай пак след малко.")


def _picture_url(code: str, article_id: int | None) -> str | None:
    """Does this article have a photo? THREE states, never a boolean:

        "<url>"  Barsy answered and there is a picture
        ""       Barsy answered and there is none
        None     we could not ask, or could not read the answer

    The third state is load-bearing. "This product has no photo" and "the ERP did not
    answer" are different facts and an operator does different things about them; folding
    the second into the first would tell someone to go and upload a photo that is already
    there.

    Read via Articles_getlist because picture_url rides in ITS extra_properties (the same
    call the catalogue sync uses); a plain Articles_get does not carry it.
    """
    if not code or not article_id:
        # Nothing to match the Barsy row ON. Not evidence of a missing picture - the third
        # state - so it must not read as one.
        return None                      # nothing to filter on — not proof of "no picture"
    try:
        rows = barsy.call("Articles_getlist",
                          {"filters": {"code": str(code)}, "length": 5,
                           "extra_properties": {"picture_url": 1}})
    except Exception as e:               # any upstream failure reads as "unknown"
        log.warning("catalog: picture check for %s failed (%s)", code, e)
        return None
    if not isinstance(rows, list):
        return None
    row = next((r for r in rows if isinstance(r, dict) and r.get("article_id") == article_id), None)
    if row is None:
        return None
    return str(row.get("picture_url") or "")


def _refuse_without_image(code: str, article_id: int | None) -> None:
    """Raise unless the article demonstrably has a picture. Fails closed on both the
    "no picture" and the "could not check" branches, with a DIFFERENT message for each."""
    pic = _picture_url(code, article_id)
    if pic is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _IMAGE_UNKNOWN)
    if not pic:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _NO_IMAGE)


class ArticleEdit(BaseModel):
    name: str
    description: str = ""
    barcode: str = ""
    oversell: bool = False        # orderable at <=0 stock (backorder / supplier dropship)
    weight_kg: str = ""           # kg → merged into Barsy extra_data (preserving SEO)
    # everything below is optional: "" / None / [] means LEAVE UNCHANGED (never clobber Barsy)
    name_public: str = ""         # storefront title (article_name_public)
    description_public: str = ""
    brand: str = ""
    attributes: list[AttrIn] = []
    cat_id: int | None = None
    amount_type_id: int | None = None
    seo_title: str = ""
    seo_description: str = ""
    status: str = ""              # "" | active | draft | withdrawn
    # --- accounting / stock behaviour (Barsy article-form parity) ---
    tax_id: int | None = None                 # ДДС група
    lot_type_id: int | None = None            # партиди
    default_package_id: int | None = None     # разфасовка
    oversell_mode: str = ""                   # "" | DEF | YES | NO — wins over the legacy `oversell`
    storemove_mode: str = ""                  # "" | DEF | YES | NO
    max_discount: float | None = None
    min_profit_percent: float | None = None
    avg_delivery_price: float | None = None
    part_factor: float | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    price_sec_currency: float | None = None   # → Barsy's misspelled actuial_price_sec_currency
    is_for_client_order: bool | None = None
    # composite_type_name / recipe_description are deliberately NOT writable — read-back only


@router.post("/{article_id:int}")
async def update_article(
    article_id: int, req: ArticleEdit,
    actor: User = Depends(require_roles("staff")),
    session: AsyncSession = Depends(get_async_session),
):
    """Update editable fields in Barsy (Barsy-parity with the create path in intake). Price is NOT
    touched here — it is edited in bulk via Ценообразуване (price policy)."""
    if not req.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Името е задължително.")
    if req.status and req.status not in _STATUS_FLAGS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден статус.")
    for mode in (req.oversell_mode, req.storemove_mode):
        if mode and mode not in _TRI:                  # refuse before touching the ERP
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна стойност (DEF/YES/NO).")
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    # ONE Articles_get up front -- used to MERGE every *_ml map (a partial {bg_BG:...} REPLACES
    # the map and drops other locales like en_US, verified) and to merge extra_data below. The
    # panel reads the ml for the English site, so name/description edits must keep every language.
    with upstream("Barsy обновяване"):
        _cur = await run_in_threadpool(
            barsy.call, "Articles_get", {"article_id": article_id, "extra_properties": {"extra_data": 1}})
    _cur = _cur if isinstance(_cur, dict) else {}
    if req.status == "active":
        # "active" is the one status that sets is_public=1, i.e. puts the product on the
        # storefront. Checked here, against the ERP, not against our cache: the cache is
        # refreshed on a sync and someone who has just uploaded a photo in Barsy would
        # otherwise be told to go and upload it again.
        _refuse_without_image(str(_cur.get("code") or ""), article_id)
    article: dict = {
        "article_name": req.name.strip(),
        "article_name_ml": _merge_name_ml(_cur.get("article_name_ml"), req.name.strip()),
    }
    if req.description:
        article["description_ml"] = _merge_name_ml(_cur.get("description_ml"), req.description)
    if req.barcode.strip():
        article["barcode"] = req.barcode.strip()  # string, not array (Barsy create/update)
    # TRI-state: the mode carries the real Barsy value (incl. DEF = "Според правата", which the old
    # YES/NO-only toggle could never restore). The legacy `oversell` bool stays the fallback so older
    # clients keep working — an explicit mode always wins.
    article["sale_below_zero"] = req.oversell_mode or ("YES" if req.oversell else "NO")
    if req.storemove_mode:
        article["storemove_below_zero"] = req.storemove_mode
    if req.name_public.strip():
        article["article_name_public"] = req.name_public.strip()
    if req.description_public.strip():
        article["description_public_ml"] = _merge_name_ml(
            _cur.get("description_public_ml"), req.description_public)
    if req.status:
        # both ints together — they drive the Shopify auto-sync (ACTIVE / Draft / Archive)
        article["is_for_sale"], article["is_public"] = _STATUS_FLAGS[req.status]
    if req.cat_id is not None:
        article["cat_id"] = article["master_cat_id"] = req.cat_id
    if req.amount_type_id is not None:
        article["amount_type_id"] = req.amount_type_id
    # Same-named Barsy fields — None means "not edited", so the key is omitted and Barsy keeps its
    # current value (sending a bare 0/"" here would silently zero out real accounting settings).
    for _f in ("tax_id", "lot_type_id", "default_package_id", "max_discount", "min_profit_percent",
               "avg_delivery_price", "part_factor", "min_amount", "max_amount"):
        _v = getattr(req, _f)
        if _v is not None:
            article[_f] = _v
    # …with ONE exception: «без разфасовка» is a real Barsy VALUE (empty string), not an absence, so
    # "not edited" and "cleared" would otherwise be indistinguishable and a package could never be
    # removed from the panel. 0 is not a valid package id (they are 1/6/11/16), so we use it as the
    # explicit CLEAR sentinel and translate it to the "" Barsy expects.
    if req.default_package_id == 0:
        article["default_package_id"] = ""
    if req.price_sec_currency is not None:
        # Barsy's OWN misspelling — "fixing" it to actual_price_sec_currency drops the write silently
        article["actuial_price_sec_currency"] = req.price_sec_currency
    if req.is_for_client_order is not None:
        # 1/0 like is_for_sale / is_public — Barsy's flag convention on write
        article["is_for_client_order"] = 1 if req.is_for_client_order else 0
    # Brand + attributes are ONE details list (parity with intake._build_barsy_article) and Barsy
    # REPLACES it wholesale — so they must travel together, and the Марка row must come first. When
    # the request carries neither, `details` is omitted entirely so the existing rows survive.
    details = [{"detail_name": a.key.strip(), "detail_value": a.value.strip()}
               for a in req.attributes if a.key.strip() and a.key.strip() != _BRAND_DETAIL]
    brand = req.brand.strip() or next(
        (a.value.strip() for a in req.attributes if a.key.strip() == _BRAND_DETAIL), "")
    if brand:
        details.insert(0, {"detail_name": _BRAND_DETAIL, "detail_value": brand})
    if details:
        article["details"] = details
    # weight + SEO ride in the native extra_data object — MERGE, don't clobber (it also holds
    # variant_codes). ONE read covers all three, however many of them changed.
    extra_new = {k: v for k, v in (("weight_kg", req.weight_kg.strip()),
                                   ("seo_title", req.seo_title.strip()),
                                   ("seo_description", req.seo_description.strip())) if v}
    if extra_new:
        extra = dict(_cur.get("extra_data") or {})
        extra.update(extra_new)
        article["extra_data"] = extra
    with upstream("Barsy обновяване"):
        await run_in_threadpool(barsy.call, "Articles_update",
                                {"article_id": article_id, "article": article})
    await log_action(session, actor, "article.update", target=str(article_id),
                     detail=f"name={req.name[:40]}")
    return {"ok": True}


# ================================ Масови операции (general mass edit) ==============================
# Mirrors Barsy's article grid mass-edit. Each field is VERIFIED against live Barsy (throwaway
# articles: create -> apply -> read-back -> delete, 2026-08-04). Write path per field:
#   "save_mass"        -> native one-call grid save (barsy_bulk.save_mass). Scalar/numeric/flag/select.
#   "articles_update"  -> per-article Articles_update with a FULL-ml-map merge (localized text with an
#                         _ml sibling the grid cannot maintain; keeps en_US etc. for the English site).
# EXCLUDED (do NOT round-trip via save_mass on this install): min_profit_percent, max_discount, slug,
# min/max_order, min/max_amount, is_for_client_order, is_for_load. Set per cell (Barsy's grid has no
# ×/÷ — relative price ops live in Ценообразуване / bulk-price).
_MASS_FIELDS = [
    {"col": "actual_price", "title": "Цена продажна", "type": "currency", "group": "Цена", "write": "save_mass"},
    {"col": "avg_delivery_price", "title": "Доставна цена", "type": "currency", "group": "Цена", "write": "save_mass"},
    {"col": "article_name", "title": "Име", "type": "text", "group": "Идентификация", "write": "articles_update"},
    {"col": "article_name_public", "title": "Публично име", "type": "text", "group": "Идентификация", "write": "articles_update"},
    {"col": "description", "title": "Описание", "type": "textarea", "group": "Идентификация", "write": "articles_update"},
    {"col": "code", "title": "Код", "type": "text", "group": "Идентификация", "write": "save_mass"},
    {"col": "barcode", "title": "Баркод", "type": "text", "group": "Идентификация", "write": "save_mass"},
    {"col": "is_for_sale", "title": "За продажба", "type": "checkbox", "group": "Флагове", "write": "save_mass", "mass_col": "is_for_sale_mass"},
    {"col": "is_public", "title": "Публичен (онлайн)", "type": "checkbox", "group": "Флагове", "write": "save_mass"},
    {"col": "is_service", "title": "Услуга", "type": "checkbox", "group": "Флагове", "write": "save_mass"},
    {"col": "tax", "title": "ДДС група", "type": "select", "group": "Данък и мерки", "write": "save_mass", "options": "tax"},
    {"col": "amount_type_id", "title": "Мерна единица", "type": "select", "group": "Данък и мерки", "write": "save_mass", "options": "amount_type"},
]
_MASS_BY_COL = {f["col"]: f for f in _MASS_FIELDS}
_NUMERIC_TYPES = {"currency", "amount", "int"}

# localized col -> (scalar field to also write or None, ml field to merge)
_LOCALIZED_ML = {
    "article_name": ("article_name", "article_name_ml"),
    "article_name_public": ("article_name_public", "article_name_public_ml"),
    "description": (None, "description_ml"),
}
# whitelist col -> ProductCache attribute (for preview old-value + write-through). Others: not cached.
_MASS_CACHE_ATTR = {"actual_price": "price", "code": "code", "barcode": "barcode",
                    "article_name": "name", "description": "description"}


def _tax_options() -> list[dict]:
    try:
        rows = barsy.call("TaxGroups_getList", {}) or []
    except Exception:
        return []
    return [{"value": str(t.get("tax_id")), "label": t.get("tax_name_full") or t.get("tax_name") or str(t.get("tax_id"))}
            for t in rows if isinstance(t, dict) and t.get("tax_id") is not None]


def _amount_options() -> list[dict]:
    try:
        rows = barsy.call("Amounttypes_getList", {}) or []
    except Exception:
        return []
    out = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        v = a.get("amount_type_id") or a.get("id")
        if v is None:
            continue
        out.append({"value": str(v), "label": a.get("amount_type_name") or a.get("name") or a.get("name_short") or str(v)})
    return out


@router.get("/mass-columns")
async def mass_columns(user: User = Depends(current_active_user)):
    """The VERIFIED whitelist of mass-editable fields (+ live select options). The Масови операции UI
    builds its field picker from this. `write` tells the client nothing — routing is server-side; it
    is exposed only so the UI can note that localized text keeps all languages."""
    tax_opts = amount_opts = None
    out = []
    for f in _MASS_FIELDS:
        d = {"col": f["col"], "title": f["title"], "type": f["type"], "group": f["group"],
             "numeric": f["type"] in _NUMERIC_TYPES, "write": f["write"]}
        if f.get("options") == "tax":
            if tax_opts is None:
                tax_opts = await run_in_threadpool(_tax_options)
            d["options"] = tax_opts
        elif f.get("options") == "amount_type":
            if amount_opts is None:
                amount_opts = await run_in_threadpool(_amount_options)
            d["options"] = amount_opts
        out.append(d)
    return {"fields": out}


class MassOpRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=_BULK_MAX)
    col: str
    value: str                       # the value to SET for all selected (checkbox "0"/"1"; select value)
    confirm: bool = False


async def _apply_localized(session, changes: list, col: str):
    """Per-article Articles_update for a localized text field: read the current *_ml map, merge the new
    value into the active locale (preserving other locales), write the scalar + FULL merged map."""
    scalar, ml_field = _LOCALIZED_ML[col]
    ids = [c["article_id"] for c in changes]
    cache = {pc.article_id: pc for pc in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(ids)))).scalars().all()}
    applied, failed = 0, []
    for i, c in enumerate(changes):
        aid = c["article_id"]
        if i:
            await _bulk.pace()
        try:
            cur = await run_in_threadpool(barsy.call, "Articles_get", {"article_id": aid})
            body: dict = {ml_field: _merge_name_ml((cur or {}).get(ml_field), c["new"])}
            if scalar:
                body[scalar] = c["new"]
            await run_in_threadpool(barsy.call, "Articles_update", {"article_id": aid, "article": body})
        except Exception:
            failed.append(c["code"])
            continue
        pc = cache.get(aid)
        if pc is not None and col in _MASS_CACHE_ATTR:
            setattr(pc, _MASS_CACHE_ATTR[col], c["new"])   # write-through
        applied += 1
    await session.commit()
    return applied, failed


@router.post("/mass-op")
async def mass_op(req: MassOpRequest, actor: User = Depends(current_owner),
                  session: AsyncSession = Depends(get_async_session)):
    """Set ONE whitelisted field to a single value across the selected products (mirrors Barsy's grid
    mass-edit). Preview first (confirm=false → the computed changes, writes nothing); confirm=true
    applies. Localized text (name/public name/description) writes via Articles_update with a full-ml
    merge so other languages survive; everything else via native save_mass. Owner-only, audited,
    gated by BARSY_WRITE_LIVE. Price DELTAS (×/÷/±) live in Ценообразуване / bulk-price, not here."""
    field = _MASS_BY_COL.get(req.col)
    if field is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Непознато поле за масова редакция.")
    val = req.value
    if field["type"] == "checkbox" and val not in ("0", "1"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Флагът приема само 0 или 1.")
    if field["type"] == "select":
        opts = _tax_options() if field.get("options") == "tax" else _amount_options()
        if opts and val not in {o["value"] for o in opts}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна стойност за списъчното поле.")

    attr = _MASS_CACHE_ATTR.get(req.col)
    rows = {pc.article_id: pc for pc in (await session.execute(
        select(ProductCache).where(ProductCache.article_id.in_(req.article_ids)))).scalars().all()}
    changes: list[dict[str, typing.Any]] = []
    for aid in req.article_ids:
        pc = rows.get(aid)
        if pc is None:
            continue
        old = getattr(pc, attr) if attr else None
        changes.append({"article_id": aid, "code": pc.code, "name": pc.name, "old": old, "new": val})
    # Putting products online in bulk is the fastest way to publish a photo-less listing, so
    # the same rule applies here. The check reads ProductCache.has_image rather than asking
    # Barsy once per selected article — a mass op can cover hundreds. That cache is refreshed
    # by the catalogue sync, so it can lag; the direction it lags in is safe (a product whose
    # photo landed after the last sync is held back, not published). Blocked rows are NAMED
    # in the response, in the preview and after the write, so nobody has to guess which of
    # their selection did not go.
    blocked: list = []
    if req.col == "is_public" and val == "1":
        allowed = []
        for c in changes:
            pc = rows.get(c["article_id"])
            if pc is not None and not pc.has_image:
                blocked.append({"article_id": c["article_id"], "code": c["code"], "name": c["name"]})
            else:
                allowed.append(c)
        changes = allowed
    if not req.confirm:
        return {"dry_run": True, "col": req.col, "value": val, "count": len(changes),
                "changes": changes, "blocked": blocked, "blocked_reason": _NO_IMAGE if blocked else ""}
    if not changes:
        return {"dry_run": False, "col": req.col, "value": val, "count": 0, "applied": 0,
                "failed": [], "blocked": blocked, "blocked_reason": _NO_IMAGE if blocked else ""}
    if not barsy_write_live():
        return {"dry_run": True, "gated": True, "note": _GATED, "col": req.col, "value": val,
                "count": len(changes), "changes": changes, "applied": 0, "failed": []}

    failed: list = []
    if field["write"] == "articles_update":
        applied, failed = await _apply_localized(session, changes, req.col)
    else:
        col_name = field.get("mass_col", req.col)
        applied = await run_in_threadpool(
            _bulk.save_mass,
            [{"article_id": c["article_id"], "col_name": col_name, "value": str(c["new"])} for c in changes])
        if attr:                                            # write-through cached cols
            cast_new = float(val) if field["type"] == "currency" else val
            changed_ids = {c["article_id"] for c in changes}
            for pc in rows.values():
                if pc.article_id in changed_ids:
                    setattr(pc, attr, cast_new)
            await session.commit()
    await log_action(session, actor, "catalog.mass_op",
                     detail=f"col={req.col} value={val} applied={applied} failed={len(failed)}")
    return {"dry_run": False, "col": req.col, "value": val, "count": len(changes),
            "applied": applied, "failed": failed, "blocked": blocked,
            "blocked_reason": _NO_IMAGE if blocked else ""}


# ================================ Barsy categories + attribute pool (#70 slice 4) =================
# The product editor picks categories FROM BARSY (single source of truth; the full Shopify taxonomy
# tree is imported there — 14.6k rows, verified 2026-08-05). The category's `slug` IS the Shopify
# taxonomy id (written by the importer), which is how the panel resolves the per-branch attribute
# schema (schemas.json) — so picking a Barsy category keeps the schema + compose-name flow verbatim.
# Categories_getlist rows do NOT carry slug; it is resolved per pick via Categories_get (one call).
_CAT_CACHE: dict = {"t": 0.0, "rows": []}          # 14.6k rows — refresh hourly, search in-process
_CAT_TTL = 3600.0
_ATTR_CACHE: dict = {"t": 0.0, "keys": {}, "values": {}}   # characteristic keys + per-key value pools
_ATTR_TTL = 300.0


def _cats_all() -> list[dict]:
    import time as _time
    now = _time.time()
    if now - _CAT_CACHE["t"] > _CAT_TTL or not _CAT_CACHE["rows"]:
        rows = barsy.call("Categories_getlist", {"filters": {}}) or []
        _CAT_CACHE["rows"] = [
            {"cat_id": c.get("cat_id"), "path": c.get("cat_path") or c.get("cat_name") or ""}
            for c in rows if isinstance(c, dict) and (c.get("cat_id") or 0) > 0 and c.get("cat_path")]
        _CAT_CACHE["t"] = now
    return _CAT_CACHE["rows"]


@router.get("/categories")
async def catalog_categories(user: User = Depends(current_active_user),
                             q: str = Query(""), limit: int = Query(30, ge=1, le=100)):
    """Search the Barsy category tree (cached in-process). Every WORD of `q` must appear in the
    path (case-insensitive) — 'чини кухня' finds kitchen plates wherever the words fall."""
    rows = await run_in_threadpool(_cats_all)
    words = [w for w in q.lower().split() if w]
    if words:
        rows = [r for r in rows if all(w in r["path"].lower() for w in words)]
    return {"categories": rows[:limit], "total": len(rows)}


@router.get("/categories/{cat_id:int}")
async def catalog_category(cat_id: int, user: User = Depends(current_active_user)):
    """One category with its slug (= Shopify taxonomy id) — resolved on pick, not in the list."""
    g = await run_in_threadpool(barsy.call, "Categories_get", {"cat_id": cat_id})
    if not isinstance(g, dict) or not g.get("cat_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категорията не е намерена.")
    return {"cat_id": g.get("cat_id"), "path": g.get("cat_path") or "", "slug": g.get("slug") or ""}


def _attr_values_for(name: str) -> list[str]:
    """Live enumerated values for a characteristic KEY by its display name (e.g. Цвят). The pool is
    global in Barsy and grows as products introduce values — merging it into the schema dropdowns is
    an owner decision (2026-08-05). Unknown key → empty (a schema-only attribute)."""
    import time as _time
    now = _time.time()
    if now - _ATTR_CACHE["t"] > _ATTR_TTL:
        try:
            keys = barsy.call("ArticleDetails_getlist", {}) or []
        except Exception:
            keys = []
        _ATTR_CACHE["keys"] = {k.get("detail_name"): k.get("key_id") for k in keys if isinstance(k, dict)}
        _ATTR_CACHE["values"] = {}
        _ATTR_CACHE["t"] = now
    kid = _ATTR_CACHE["keys"].get(name)
    if kid is None:
        return []
    if name not in _ATTR_CACHE["values"]:
        try:
            vals = barsy.call("ArticleDetails_getKeyValues", {"key_id": kid}) or []
        except Exception:
            vals = []
        _ATTR_CACHE["values"][name] = [v.get("detail_value") for v in vals
                                       if isinstance(v, dict) and v.get("detail_value")]
    return _ATTR_CACHE["values"][name]


@router.get("/attr-values")
async def catalog_attr_values(user: User = Depends(current_active_user), key: str = Query(...)):
    return {"values": await run_in_threadpool(_attr_values_for, key)}


# ================================ article suppliers (#70 slice 5) =================================
# The merged editor's SupplierTable in EDIT mode works on LIVE article-supplier links. Read via the
# articles_suppliers page (the same call intake's push uses); write via Articles_save_suppliers,
# which is FULL-REPLACE (#77) — correct here because the PUT restates the WHOLE list the user just
# loaded and edited (unlike intake's push, which must merge with links it never showed).

def _article_supplier_rows(article_id: int) -> list[dict]:
    cur = barsy.call("articles_suppliers", {
        "id": article_id, "action_type": "values",
        "active_struct_id": "eStructListForm_suppliers",
        "params": {"id": article_id}, "rows": 5000})
    out = []
    for r in ((cur or {}).get("rows") or []):
        if not r.get("supplier_id"):
            continue
        sid = int(r["supplier_id"])
        out.append({
            "id": sid, "supplier_id": sid,
            "supplier": r.get("supplier_name") or "",
            "supplier_code": str(r.get("remote_ref_id") or ""),
            "supplier_name": str(r.get("remote_article_name") or ""),
            "is_default": r.get("is_default") in (1, "1", True),
            "delivery_price": str(r.get("default_delivery_price") or ""),
            "package_id": str(r.get("package_id") or ""),
        })
    return out


@router.get("/{article_id:int}/suppliers")
async def get_article_suppliers(article_id: int, user: User = Depends(current_active_user)):
    with upstream("Barsy доставчици"):
        rows = await run_in_threadpool(_article_supplier_rows, article_id)
    return {"suppliers": rows}


class ArticleSupplierRowIn(BaseModel):
    supplier: str = ""                 # display name — resolved against Barsy's register for NEW rows
    supplier_id: int | None = None     # present on rows loaded from Barsy — used verbatim
    supplier_code: str = ""
    supplier_name: str = ""
    is_default: bool = False
    delivery_price: str = ""
    package_id: str = ""


class ArticleSuppliersIn(BaseModel):
    rows: list[ArticleSupplierRowIn] = Field(default_factory=list, max_length=50)


@router.put("/{article_id:int}/suppliers")
async def put_article_suppliers(article_id: int, req: ArticleSuppliersIn,
                                actor: User = Depends(require_roles("staff")),
                                session: AsyncSession = Depends(get_async_session)):
    """Replace the article's supplier links with exactly this list. New rows (no supplier_id) resolve
    by name against Barsy's register (the intake fold rules) — unresolvable names are reported, never
    guessed. An empty list is an INTENTIONAL clear; a list where nothing resolved is a 400 so a typo
    can never wipe real links. Exactly one Основен (first flagged wins; else the first row)."""
    from .intake import _fold_supplier
    resolved: list[dict] = []
    skipped: list[str] = []
    by_fold: dict[str, list] = {}
    if any(r.supplier_id is None for r in req.rows):
        cat = await run_in_threadpool(barsy.call, "Suppliers_getlist", {"extra_properties": ["min"]})
        for b in (cat if isinstance(cat, list) else []):
            if b.get("delete_flag"):
                continue
            by_fold.setdefault(_fold_supplier(b.get("supplier_name", "")), []).append(b)
    for r in req.rows:
        sid, reg_name = r.supplier_id, r.supplier
        if sid is None:
            f = _fold_supplier(r.supplier)
            hits = by_fold.get(f) or []
            if not hits:
                hits = [b for k, lst in by_fold.items() if f and k.startswith(f) for b in lst]
            if len(hits) != 1:
                skipped.append(r.supplier)
                continue
            sid = int(hits[0]["supplier_id"])
            reg_name = hits[0].get("supplier_name", "")
        resolved.append({
            "supplier_id": int(sid), "supplier_name": reg_name,
            "is_default": 1 if r.is_default else 0,
            "default_delivery_price": (r.delivery_price or "").strip(),
            "package_id": (r.package_id or "").strip(),
            "remote_ref_id": (r.supplier_code or "").strip(),
            "remote_article_name": (r.supplier_name or "").strip(),
        })
    if req.rows and not resolved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Никой от доставчиците не е разпознат: " + ", ".join(skipped))
    seen_def = False
    for x in resolved:
        if x["is_default"] and not seen_def:
            seen_def = True
        else:
            x["is_default"] = 0
    if resolved and not seen_def:
        resolved[0]["is_default"] = 1
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED,
                "count": len(resolved), "skipped": skipped}
    with upstream("Barsy доставчици"):
        await run_in_threadpool(barsy.call, "Articles_save_suppliers",
                                {"id": article_id, "action_type": "save", "values": {}, "rows": resolved})
    await log_action(session, actor, "article.suppliers", target=str(article_id),
                     detail=f"rows={len(resolved)} skipped={len(skipped)}")
    return {"ok": True, "count": len(resolved), "skipped": skipped}


# ====================================================================== category tree (#85c) ===
# Owner decision 2026-08-05: full tree CRUD; owner+manager write, others read-only; slug (= Shopify
# taxonomy id) IMMUTABLE; deletes guarded; every write audited. ALL write contracts were verified
# on a THROWAWAY category (19640), never on real taxonomy nodes:
#   * page-save (categories_edit action_type:save) is BROKEN on this install (HTTP 500 both create
#     and update) — writes go through the importer's own methods instead:
#   * CREATE  = Categories_create {"cat": {...}} (the method that built all 14,606 taxonomy nodes;
#     duplicate name/slug → 501).
#   * UPDATE  = Categories_update {"cat_id": <TOP LEVEL>, "cat": {...}} — rename, move (parent_id),
#     cat_name_public + description all verified; slug stays untouched when omitted.
#   * DELETE  = does NOT exist on this install („Тази структура няма дефинирана функция триене";
#     Categories_delete 501/500s on every body shape; Categories_remove is unknown) → honest 501.
#   * THROTTLE: category writes take MINUTES on this tier and a client timeout does NOT mean
#     failure (a timed-out move landed) → long timeout + re-read-to-confirm before erroring.
# Slug policy (owner-confirmed): parent slug + "-x<N>" — official taxonomy segments are numeric,
# so -x can never collide with a future Shopify release; attribute-schema inheritance = strip the
# trailing -x segments to reach the nearest official ancestor.

_TREE_TTL = 600.0
_TREE_CACHE: dict = {"t": 0.0, "root": None}   # slimmed Categories_getTree (raw is ~9.8 MB)
_CAT_WRITE_TIMEOUT = 280                        # seconds; see THROTTLE note above


def _invalidate_category_caches() -> None:
    _CAT_CACHE["t"] = 0.0
    _TREE_CACHE["t"] = 0.0
    _TREE_CACHE["root"] = None


def _slim_cat_node(n: dict) -> dict:
    """getTree node → the fields the panel needs, UNWRAPPING Barsy's envelope.

    Barsy nests every node as {"category": {...fields...}, "categories": [...children...]}. This
    used to read the fields straight off `n`, which is the wrapper for every node except the root
    (_tree_root unwraps that one by hand), so all 14,606 children came back with cat_id=None and
    slug="" — measured on the live box on 2026-08-07:

        panel's own slimming, real payload:  {"slimmed_nodes": 14606, "with_a_slug": 0}
        unwrapping instead:                  {"nodes": 14606, "with_a_slug": 14606}

    The empty defaults are what hid it. A field that quietly becomes "" when the lookup misses
    cannot tell you the lookup missed — a defensive construct that degrades to a falsy value is
    not a guard, and here it turned a structural mismatch into plausible-looking empty data.

    Accepts BOTH shapes: `n` may be the wrapper or the inner category dict, so _tree_root's
    hand-built root still works unchanged.

    cat_picture IS carried here, and holds the banner's FILE NAME — not a URL and not an id.
    Resolve it against Files_getList {obj_type: "categories", obj_property: "PICTURE", obj_id},
    whose records expose ready-made `url` and `thumbnail_url`.

    It was briefly removed on 2026-08-07 after a measurement found it null. The measurement was
    correct and the conclusion was not: it was taken on a category whose main picture had been
    uploaded and then DELETED, and deleting the main picture clears cat_picture WITHOUT promoting
    any remaining file. Re-measured on a clean category straight after an upload, both
    Categories_Get and Categories_getTree carry it. One category with an unusual history had been
    read as the general case.

    THREE-VALUED, so do not treat empty as "no banner":
      * non-empty                              -> that filename is the banner
      * empty BUT Files_getList non-empty      -> orphaned main; fall back to the first file or
                                                  ask the owner to choose. NOT "no banner".
      * empty AND Files_getList empty          -> genuinely none; serve the default.

    The admin UI masks the orphaned case by rendering the first file as Главна снимка, so the
    screen and the API disagree without either being wrong. Full capture:
    docs/BARSY_CATEGORY_FILES_AND_DELETE_20260807.md
    """
    wrapped = n.get("category")
    inner: dict = wrapped if isinstance(wrapped, dict) else n
    kids = n.get("categories") or inner.get("categories") or []
    return {"cat_id": inner.get("cat_id"), "parent_id": inner.get("parent_id"),
            "name": inner.get("cat_name") or "", "slug": inner.get("slug") or "",
            "child_articles_cnt": int(inner.get("child_articles_cnt") or 0),
            # The banner's FILENAME (not a URL, not an id) — resolve it against Files_getList's
            # `url`. May legitimately be empty while the category still HAS pictures; see above.
            "cat_picture": inner.get("cat_picture") or "",
            "children": [_slim_cat_node(c) for c in kids if isinstance(c, dict)]}


def _tree_root(force: bool = False) -> dict:
    now = time.time()
    if force or _TREE_CACHE["root"] is None or now - _TREE_CACHE["t"] > _TREE_TTL:
        raw = barsy.call("Categories_getTree", {})
        raw = raw if isinstance(raw, dict) else {}
        root = dict(raw.get("category") or {})
        root["categories"] = raw.get("categories") or []
        _TREE_CACHE["root"] = _slim_cat_node(root)
        _TREE_CACHE["t"] = now
    return _TREE_CACHE["root"]


def _find_cat(node: dict, cat_id: int):
    if node.get("cat_id") == cat_id:
        return node
    for c in node.get("children") or []:
        hit = _find_cat(c, cat_id)
        if hit is not None:
            return hit
    return None


def _new_cat_id(out) -> int | None:
    if isinstance(out, int):
        return out
    if isinstance(out, dict):
        v = out.get("cat_id")
        return int(v) if v not in (None, "") else None
    return None


# ------------------------------------------------------------------- category visibility ---
# Barsy has NO visibility field on a category (schema captured 2026-08-07), so this is panel-side
# state. The banner went the other way for the opposite reason: Barsy HAS a picture field.


def _resolve_enabled_slugs(root: dict, toggles: dict) -> set:
    """Which categories are ACTUALLY on, given the owner's toggles and the live tree.

        resolved(node) = own row says True  AND  no ANCESTOR row says False

    The asymmetry is load-bearing and was nearly got wrong. A missing row means "off" for the node
    itself, but it must be NEUTRAL for that node's descendants. The owner's default-on set is
    hg-1..hg-21, whose parent `hg` is deliberately untouched — if a missing ancestor row counted as
    disabled, all 21 would resolve to off and the owner's stated configuration could not be
    expressed at all. Explicitly switching `hg` off, on the other hand, must take all 21 with it.

    One pass over the tree, carrying the ancestor verdict down. The taxonomy is 14 606 nodes, so a
    per-node ancestor walk would be quadratic; this is linear.
    """
    out: set = set()
    stack = [(root, False)]                      # (node, an ancestor said False)
    while stack:
        node, blocked = stack.pop()
        slug = (node.get("slug") or "").strip()
        verdict = toggles.get(slug) if slug else None
        if verdict is True and not blocked:
            out.add(slug)
        # `blocked` for the children: inherited, or set here by an explicit False.
        child_blocked = blocked or (verdict is False)
        for c in node.get("children") or []:
            if isinstance(c, dict):
                stack.append((c, child_blocked))
    return out


def _slug_to_id(root: dict) -> dict:
    """slug -> cat_id over the live tree. The storefront asked for ids; the panel stores slugs."""
    out: dict = {}
    stack = [root]
    while stack:
        node = stack.pop()
        slug = (node.get("slug") or "").strip()
        if slug and node.get("cat_id") is not None:
            out[slug] = node["cat_id"]
        for c in node.get("children") or []:
            if isinstance(c, dict):
                stack.append(c)
    return out


async def _toggles(session: AsyncSession) -> dict:
    rows = (await session.execute(select(CategoryVisibility))).scalars().all()
    return {r.slug: bool(r.enabled) for r in rows}


# The storefront feed's own cache. Short TTL because this is the gate on what the public can see:
# a stale ENABLE is a category that appears late, but a stale DISABLE is a product still selling
# from a category the owner switched off. Writes invalidate it outright rather than waiting.
_ENABLED_FEED_TTL = 120.0
_ENABLED_FEED: dict = {"t": 0.0, "payload": None}


def _invalidate_enabled_feed() -> None:
    _ENABLED_FEED["t"] = 0.0
    _ENABLED_FEED["payload"] = None


async def build_enabled_feed(session: AsyncSession) -> dict:
    """The authoritative answer to "which categories are on", for the storefront.

    Raises rather than returning an empty set when it cannot work the answer out — Barsy
    unreachable, tree unparseable. The caller turns that into a 503. Returning {"ids": []} on
    failure would be indistinguishable from the owner having enabled nothing, and the storefront
    would have to assume the worse of the two every time the ERP hiccuped.

    `ids` and `slugs` are the same categories twice: the panel stores slugs (immutable taxonomy
    ids), the storefront asked for cat_ids. count == len(ids) always.
    """
    now = time.time()
    cached = _ENABLED_FEED["payload"]
    if cached is not None and now - _ENABLED_FEED["t"] <= _ENABLED_FEED_TTL:
        return cached
    root = await run_in_threadpool(_tree_root)
    if not isinstance(root, dict) or not root:
        raise RuntimeError("category tree unavailable")
    toggles = await _toggles(session)
    resolved = _resolve_enabled_slugs(root, toggles)
    by_slug = _slug_to_id(root)
    slugs = sorted(resolved)
    # Every resolved slug came FROM the tree, so this lookup cannot miss.
    ids = [by_slug[s] for s in slugs]
    # ids and slugs describe the SAME categories, so they stay the same length and count is
    # len(ids). The storefront asked which to trust if they could ever disagree; they cannot.
    #
    # Orphans are computed from the TOGGLES, not from the resolved set — a slug Barsy no longer
    # has is absent from the tree, so it never resolves and could never show up on that side. It
    # is a category the owner believes is published and that has quietly stopped existing, which
    # is exactly the thing that must not be silent.
    orphaned = sorted(s for s, on in toggles.items() if on and s not in by_slug)
    payload = {"ids": ids, "slugs": slugs, "count": len(ids),
               "orphaned_slugs": orphaned,
               "cached_at": now}
    _ENABLED_FEED["payload"] = payload
    _ENABLED_FEED["t"] = now
    return payload


class CategoryEnabledIn(BaseModel):
    enabled: bool


@router.get("/categories/visibility")
async def category_visibility(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """The owner's toggles, plus what they RESOLVE to against the live tree.

    Both, on purpose. `toggles` is what was clicked; `enabled_slugs` is what the storefront will
    actually see. They differ whenever a parent is switched off, and showing only the first is how
    an operator ends up insisting a category is on while the site disagrees.
    """
    with upstream("Barsy категории"):
        root = await run_in_threadpool(_tree_root)
    toggles = await _toggles(session)
    resolved = _resolve_enabled_slugs(root, toggles)
    return {"toggles": toggles, "enabled_slugs": sorted(resolved), "count": len(resolved)}


@router.put("/categories/visibility/{slug}")
async def set_category_enabled(
    slug: str, req: CategoryEnabledIn,
    actor: User = Depends(require_roles("manager")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Switch a category on or off for the website. Manager and up; audited.

    Writes an EXPLICIT row either way. Setting False is not the same as deleting the row: False
    cascades to descendants, absence does not.
    """
    slug = (slug or "").strip()
    if not slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Липсва slug на категория.")
    row = (await session.execute(select(CategoryVisibility).where(
        CategoryVisibility.slug == slug))).scalar_one_or_none()
    if row is None:
        row = CategoryVisibility(slug=slug)
        session.add(row)
    row.enabled = bool(req.enabled)
    row.updated_by = actor.email or ""
    row.updated_at = datetime.datetime.now(datetime.UTC)
    await session.commit()
    await log_action(session, actor, "catalog.category_visibility", target=slug,
                     detail=f"enabled={row.enabled}")
    # The feed caches. A disable that takes ten minutes to land is a product still selling from a
    # category the owner just switched off, so the write invalidates rather than waiting out a TTL.
    _invalidate_enabled_feed()
    return {"ok": True, "slug": slug, "enabled": row.enabled}


@router.delete("/categories/visibility/{slug}")
async def clear_category_enabled(
    slug: str,
    actor: User = Depends(require_roles("manager")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Back to NEUTRAL — off for itself, no longer forcing its descendants off.

    Without this the third state is unreachable: an ancestor switched off could never be returned
    to "not my business" without switching it on, which would be a different thing entirely.
    """
    slug = (slug or "").strip()
    row = (await session.execute(select(CategoryVisibility).where(
        CategoryVisibility.slug == slug))).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()
    await log_action(session, actor, "catalog.category_visibility", target=slug, detail="cleared")
    _invalidate_enabled_feed()
    return {"ok": True, "slug": slug, "enabled": None}


@router.get("/categories/tree")
async def category_tree(user: User = Depends(current_active_user),
                        session: AsyncSession = Depends(get_async_session)) -> dict:
    """Дървото на категориите — слим прокси над Categories_getTree (кеш ~10 мин; инвалидира се
    при запис от панела и от категорийния webhook през _CAT_CACHE reset)."""
    with upstream("Barsy категории"):
        root = await run_in_threadpool(_tree_root)
    # Returned ALONGSIDE the tree, never written into it: `root` is the shared ~10-minute cache,
    # and stamping one request's resolved flags into those dicts would serve them to everyone.
    resolved = _resolve_enabled_slugs(root, await _toggles(session))
    return {"tree": root, "enabled_slugs": sorted(resolved)}


class CategoryCreateIn(BaseModel):
    parent_id: int
    name: str
    name_public: str = ""
    description: str = ""


class CategoryPatchIn(BaseModel):
    name: str = ""
    name_public: str = ""
    description: str = ""
    parent_id: int | None = None     # move


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(
    req: CategoryCreateIn,
    actor: User = Depends(require_roles("manager")),
    session: AsyncSession = Depends(get_async_session),
):
    """Нова категория под съществуващ родител. Slug = родителският slug + "-x<N>" (наша
    номерация — официалните Shopify сегменти са само числа, затова -x никога не се сблъсква с
    бъдещо тяхно издание); схемата на атрибутите се наследява от най-близкия официален предшественик."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Името е задължително.")
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    with upstream("Barsy категории"):
        parent = await run_in_threadpool(barsy.call, "Categories_get", {"cat_id": req.parent_id})
    if not isinstance(parent, dict) or parent.get("cat_id") is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Родителската категория не е намерена.")
    # mint the -x slug from the parent's CURRENT children (fresh tree — creates are rare and the
    # write itself is minutes-slow anyway, so a fresh 9.8MB read here is noise)
    base = (parent.get("slug") or "").strip() or "dx"
    with upstream("Barsy категории"):
        root = await run_in_threadpool(_tree_root, True)
    pnode = _find_cat(root, req.parent_id)
    used = []
    for c in (pnode.get("children") if pnode else []) or []:
        m = re.match(re.escape(base) + r"-x(\d+)$", c.get("slug") or "")
        if m:
            used.append(int(m.group(1)))
    slug = f"{base}-x{(max(used) + 1) if used else 1}"
    cat: dict = {"cat_name": name, "cat_name_ml": {"bg_BG": name},
                 "cat_name_public": {"bg_BG": req.name_public.strip() or name},
                 "slug": slug, "parent_id": req.parent_id}
    if req.description.strip():
        cat["description"] = {"bg_BG": req.description.strip()}
    with upstream("Barsy категории"):
        out = await run_in_threadpool(barsy.call, "Categories_create", {"cat": cat},
                                      timeout=_CAT_WRITE_TIMEOUT)
    cid = _new_cat_id(out)
    if not cid:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Barsy не върна cat_id при създаването ({str(out)[:80]}).")
    _invalidate_category_caches()
    await log_action(session, actor, "category.create", target=name,
                     detail=f"cat_id={cid} · slug={slug} · родител {req.parent_id}")
    return {"ok": True, "cat_id": cid, "slug": slug}


@router.patch("/categories/{cat_id:int}")
async def update_category(
    cat_id: int, req: CategoryPatchIn,
    actor: User = Depends(require_roles("manager")),
    session: AsyncSession = Depends(get_async_session),
):
    """Преименуване / описание / преместване. Slug НИКОГА не се изпраща (таксономичното id е
    неизменимо — преименуването сменя само изписването); ml картите се сливат ЦЕЛИ (частична
    {bg_BG} карта би изтрила другите езици — същото правило като при артикулите)."""
    wants = [req.name.strip(), req.name_public.strip(), req.description.strip()]
    if not any(wants) and req.parent_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Няма подадени промени.")
    if req.parent_id is not None and req.parent_id == cat_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Категорията не може да е свой родител.")
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    with upstream("Barsy категории"):
        cur = await run_in_threadpool(barsy.call, "Categories_get", {"cat_id": cat_id})
    if not isinstance(cur, dict) or cur.get("cat_id") is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категорията не е намерена.")
    cat: dict = {}
    if req.name.strip():
        cat["cat_name"] = req.name.strip()
        cat["cat_name_ml"] = _merge_name_ml(cur.get("cat_name_ml"), req.name.strip())
    if req.name_public.strip():
        cat["cat_name_public"] = _merge_name_ml(cur.get("cat_name_public_ml"), req.name_public.strip())
    if req.description.strip():
        cat["description"] = _merge_name_ml(cur.get("description_ml"), req.description.strip())
    if req.parent_id is not None:
        cat["parent_id"] = req.parent_id
    note = ""
    try:
        with upstream("Barsy категории"):
            await run_in_threadpool(barsy.call, "Categories_update",
                                    {"cat_id": cat_id, "cat": cat}, timeout=_CAT_WRITE_TIMEOUT)
    except HTTPException:
        # THROTTLE tier: a timed-out write can still LAND (verified live on the throwaway) —
        # re-read before declaring failure, else the user retries and we double-write.
        with upstream("Barsy категории"):
            after = await run_in_threadpool(barsy.call, "Categories_get", {"cat_id": cat_id})
        after = after if isinstance(after, dict) else {}
        applied = ((("cat_name" not in cat) or after.get("cat_name") == cat["cat_name"]) and
                   (("parent_id" not in cat) or after.get("parent_id") == cat["parent_id"]))
        if not applied:
            raise
        note = "Barsy отговори бавно — промяната е потвърдена с повторно четене."
    _invalidate_category_caches()
    await log_action(session, actor, "category.update", target=str(cat_id),
                     detail=" · ".join(k for k in cat if k != "cat_name_ml"))
    return {"ok": True, **({"note": note} if note else {})}


@router.delete("/categories/{cat_id:int}")
async def delete_category(
    cat_id: int,
    actor: User = Depends(require_roles("manager")),
    session: AsyncSession = Depends(get_async_session),
):
    """Изтриване по договора, ЗАСНЕТ от собствения UI на Barsy (Claude-in-Chrome capture,
    2026-08-05 — правилото на собственика: при съмнение за метод се заснема истинската заявка):
    Categories_Delete {"cats": [{"id": N, "parent_id": "P", "weight": 1}], "mode": "0"}.
    Ключът на елемента е `id` (НЕ cat_id) + mode на горно ниво — затова слепите проби опираха на
    „Липсват данни за категории". mode: "0" = без артикули/модификатори (нашият гард така или
    иначе отказва непразни категории), "1" = заедно с артикулите, "2" = местене преди изтриване
    (не ги ползваме). RESULT-verified: две тестови категории изтрити през същия договор."""
    with upstream("Barsy категории"):
        root = await run_in_threadpool(_tree_root)
    node = _find_cat(root, cat_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категорията не е намерена.")
    kids, arts = len(node.get("children") or []), int(node.get("child_articles_cnt") or 0)
    if kids or arts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Категорията съдържа {arts} артикула и {kids} подкатегории — "
                            f"преместете ги първо.")
    if not barsy_write_live():
        return {"ok": True, "dry_run": True, "gated": True, "note": _GATED}
    body = {"cats": [{"id": cat_id, "parent_id": str(node.get("parent_id") or ""), "weight": 1}],
            "mode": "0"}
    try:
        with upstream("Barsy категории"):
            await run_in_threadpool(barsy.call, "Categories_Delete", body,
                                    timeout=_CAT_WRITE_TIMEOUT)
    except HTTPException:
        pass    # timeout ≠ failure on this tier — the absence check below is the verdict
    with upstream("Barsy категории"):
        rows = await run_in_threadpool(barsy.call, "Categories_getlist", {})
    if any(isinstance(c, dict) and c.get("cat_id") == cat_id for c in (rows or [])):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Barsy не потвърди изтриването — категорията още съществува.")
    _invalidate_category_caches()
    await log_action(session, actor, "category.delete", target=str(cat_id),
                     detail=f"родител {node.get('parent_id')}")
    return {"ok": True}
