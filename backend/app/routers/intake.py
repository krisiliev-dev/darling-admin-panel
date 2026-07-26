# -*- coding: utf-8 -*-
"""Product intake — the signature module. Turns a bare stock name (and, later, a photo)
into a draft: category → attributes → clean name → description. The AI draft is the panel's
'pencil' — grey and overwriteable; the human commits in 'pen'.

Enrichment reuses the pipeline's FREE-QUOTA, guarded model chain (generate_with_chain →
run_direct, which honours the NEVER_CALL blocklist, exhausted-model list and DashScope
opt-in). It does NOT touch the live fleet's task/shard/telemetry-mutation machinery — it's a
single, isolated, low-volume call per product."""
import json
import os
import re
import time
from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func as safunc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

import datetime as _dt

from ..audit import log_action
from ..config import get_settings
from ..core import barsy
from ..db import get_async_session
from ..models import DeliveryBatch, DeliveryLine, DraftImage, IntakeDraft, NameRecipe, User
from ..users import current_active_user, current_owner

router = APIRouter(prefix="/api/intake", tags=["intake"])

# Barsy amount_type_id map (verified contract, see barsy-api memory)
_UNIT_ID = {"бр": 1, "л": 2, "кг": 3, "ч": 4, "д": 5, "км": 6, "м": 7, "мин": 8,
            "м2": 11, "м3": 16}


class EnrichRequest(BaseModel):
    name: str          # the stock/till name to enrich
    hint: str = ""     # optional extra context from staff


class Attribute(BaseModel):
    key: str
    value: str


class EnrichDraft(BaseModel):
    category: str
    name: str
    attributes: list[Attribute]
    description: str
    meta_title: str = ""
    meta_description: str = ""
    model: str
    elapsed_ms: int


def _prompt(name: str, hint: str, categories: list[str]) -> str:
    hint_line = f"Допълнителна подсказка от служителя: {hint}\n" if hint.strip() else ""
    return (
        "Ти си асистент за български онлайн магазин за домашни стоки. За продукта по-долу "
        "върни САМО валиден JSON без коментар, точно с този вид:\n"
        '{"category":"<най-подходяща категория>","name":"<чисто търговско име на български>",'
        '"attributes":[{"key":"Материал","value":"..."},{"key":"Цвят","value":"..."}],'
        '"description":"<кратко продажно описание на български, 2-3 изречения, без цена>",'
        '"meta_title":"<SEO заглавие до 60 знака>","meta_description":"<SEO описание до 155 знака>"}\n\n'
        "Правила: категорията избери от списъка (или най-близката); чист български без руски/"
        "сръбски думи; не измисляй цена, количество или марка, ако не са дадени.\n"
        f"Възможни категории: {categories}\n\n"
        f"Продукт (име от касата): {name}\n{hint_line}"
    )


@router.get("/categories")
def categories(user=Depends(current_active_user)):
    """The real catalog taxonomy — lets the human re-pick the category in 'pen'."""
    import run_one
    return {"categories": run_one.known_categories()}


# ---------------------------------------------------- Shopify product taxonomy (category picker) ---
# The formal Shopify taxonomy, precomputed to SLIM indexes bundled in the panel repo (so it's always
# deployed with the panel — see app/data/shopify_taxonomy, built by scripts/precompute_taxonomy.py):
#   categories.json  [{id, en, bg, leaf}]              — EN + BG breadcrumb per category
#   cat_attrs.json   {category_id: [attribute_handle]} — which attributes a category has
#   attributes.json  {handle: {en, bg, free, values}} — attribute defs + allowed values (EN + BG)
_TAX_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "shopify_taxonomy")


def _load_tax(name: str):
    try:
        with open(os.path.join(_TAX_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _tax_categories() -> list:
    return _load_tax("categories.json") or []


@lru_cache(maxsize=1)
def _tax_schemas_raw() -> dict:
    """The v3 per-leaf attribute schema, value-lists interned into a shared pool to keep the file
    small: {"pool": [[values...]], "leaves": {id: [[name, handle, pool_idx, free]]}}."""
    return _load_tax("schemas.json") or {}


def _leaf_schema(cid: str) -> list:
    """Expand ONE category's schema → [{name(BG), handle, values:[BG], free}] (lazy, low-memory)."""
    raw = _tax_schemas_raw()
    pool = raw.get("pool") or []
    out = []
    for r in (raw.get("leaves") or {}).get(cid) or []:      # r = [name, handle, pool_idx, free]
        vals = pool[r[2]] if isinstance(r[2], int) and 0 <= r[2] < len(pool) else []
        out.append({"name": r[0], "handle": r[1], "values": vals, "free": r[3]})
    return out


async def _vertical_weights(session: AsyncSession) -> dict:
    """{vertical -> weight} biasing the category picker toward where our products already sit. Counts
    the verticals of products' assigned Shopify categories (from the drafts) and seeds Home & Garden so
    it leads pre-launch; shifts on its own as the product mix changes."""
    weights = {"hg": 1}                      # Darling is home & garden → biased there by default
    rows = (await session.execute(
        select(IntakeDraft.shopify_category_id).where(IntakeDraft.shopify_category_id != ""))).all()
    for (cid,) in rows:
        v = (cid or "").split("-")[0]
        if v:
            weights[v] = weights.get(v, 0) + 1
    return weights


@router.get("/shopify-categories")
async def shopify_categories(q: str = "", limit: int = 40, user=Depends(current_active_user),
                             session: AsyncSession = Depends(get_async_session)):
    """Search the Shopify product taxonomy by BG or EN name. Returns [{id, name, path, en}], BIASED
    toward the verticals most of our products fall into (dynamic — see _vertical_weights). Empty q
    returns the top-level verticals so the picker is never blank."""
    cats = _tax_categories()
    if not cats:
        return {"categories": [], "available": False}
    ql = q.strip().lower()
    lim = max(1, min(200, limit))
    weights = await _vertical_weights(session)
    items = []
    for c in cats:
        if ql:
            if ql not in (c.get("bg") or "").lower() and ql not in (c.get("en") or "").lower():
                continue
        elif "-" in c["id"]:                 # empty query → only the top-level verticals
            continue
        bg = c.get("bg") or c.get("en") or ""
        items.append({"id": c["id"], "name": bg.split(" > ")[-1], "path": bg, "en": c.get("en", "")})
    # heavier verticals first, then shallower/alphabetical
    items.sort(key=lambda x: (-weights.get(x["id"].split("-")[0], 0), len(x["path"]), x["path"]))
    return {"categories": items[:lim], "available": True}


@router.get("/shopify-categories/{cid}/attributes")
def shopify_category_attributes(cid: str, user=Depends(current_active_user)):
    """The attribute SCHEMA for a category: each attribute is either a picklist (free=false → dropdown
    of allowed BG values) or free text (free=true → e.g. dimensions). Drives the dynamic attribute
    fields in the product editor."""
    if not _tax_schemas_raw():
        return {"attributes": [], "available": False}
    out = []
    for g in _leaf_schema(cid):
        out.append({"handle": g["handle"], "name": g["name"],
                    "role": _role_for_handle(g["handle"], g["name"]), "free": g["free"],
                    "values": [{"h": v, "name": v} for v in g["values"]]})
    return {"attributes": out, "available": True}


# map a taxonomy attribute to the v3 name-composer's canonical role (Color/Material/Shape/Feature)
_ROLE_KEYWORDS = [("colour", "Color"), ("color", "Color"), ("material", "Material"),
                  ("shape", "Shape"), ("feature", "Feature")]


def _role_for_handle(handle: str, en: str) -> str:
    s = f"{handle} {en}".lower()
    for kw, role in _ROLE_KEYWORDS:
        if kw in s:
            return role
    return ""


class ComposeNameRequest(BaseModel):
    category_id: str = ""
    brand: str = ""                     # prepended verbatim (proper noun) if present
    attributes: dict[str, str] = {}    # {BG attribute name: BG value} as filled in the editor
    capacity: str = ""                  # вместимост
    dimensions: str = ""                # размери


async def _recipe_style(session: AsyncSession, leaf_id: str = "") -> dict:
    """Effective naming recipe = the global style overlaid by the leaf's own (if any)."""
    ids = [""] if not leaf_id else ["", leaf_id]
    rows = (await session.execute(select(NameRecipe).where(NameRecipe.leaf_id.in_(ids)))).scalars().all()
    by = {r.leaf_id: json.loads(r.style_json or "{}") for r in rows}
    return {**by.get("", {}), **(by.get(leaf_id, {}) if leaf_id else {})}


def _facts_from(cat_bg: str, brand: str, attributes: dict, capacity: str = "", dimensions: str = "") -> dict:
    """Build the v3 composer facts from a leaf breadcrumb + BG attributes (schema roles resolved by the
    composer's own aliases, so we pass the BG {name: value} straight through)."""
    return {"leaf_bg": cat_bg or "", "brand": (brand or "").strip(), "attributes": attributes or {},
            "вместимост": capacity, "размери": dimensions}


@router.post("/compose-name")
async def compose_name(req: ComposeNameRequest, user=Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    """Deterministically compose the Bulgarian product name from the Shopify leaf + attributes + size,
    using the v3 composer (app/naming.py) under the leaf's naming RECIPE (per-leaf default style, else
    global). Derived — recomputes as fields change."""
    from .. import naming
    cats = {c["id"]: c for c in _tax_categories()}
    cat = cats.get(req.category_id)
    if not cat:
        return {"name": "", "available": bool(cats)}
    canonical: dict[str, str] = {}
    for g in _leaf_schema(req.category_id):
        role = _role_for_handle(g["handle"], g["name"])
        if role and req.attributes.get(g["name"]):
            canonical[role] = req.attributes[g["name"]]
    facts = _facts_from(cat.get("bg") or "", req.brand, canonical, req.capacity, req.dimensions)
    style = await _recipe_style(session, req.category_id)
    return {"name": naming.build(facts, style or None), "available": True}


# ---------------------------------------------------------------- naming recipes (style) ---
class RecipeIn(BaseModel):
    style: dict[str, str] = {}


@router.get("/recipe/options")
def recipe_options(user=Depends(current_active_user)):
    """The editable recipe knobs: {attribute: {default, variants}} (e.g. Color: prefix|suffix|…)."""
    from .. import naming
    return {"options": naming.style_options()}


@router.get("/recipe")
async def get_recipe(leaf: str = "", user=Depends(current_active_user),
                     session: AsyncSession = Depends(get_async_session)):
    """The global recipe + (optionally) a leaf's override + the effective merge."""
    glob = (await session.execute(select(NameRecipe).where(NameRecipe.leaf_id == ""))).scalar_one_or_none()
    leaf_row = (await session.execute(select(NameRecipe).where(NameRecipe.leaf_id == leaf))).scalar_one_or_none() if leaf else None
    return {"global": json.loads(glob.style_json) if glob else {},
            "leaf": json.loads(leaf_row.style_json) if leaf_row else {},
            "effective": await _recipe_style(session, leaf)}


@router.put("/recipe")
async def put_recipe(req: RecipeIn, leaf: str = "", actor: User = Depends(current_owner),
                     session: AsyncSession = Depends(get_async_session)):
    """Set the global (leaf="") or a per-leaf naming recipe. New products in that leaf auto-apply it."""
    style = {k: v for k, v in req.style.items() if v}          # drop cleared knobs
    row = await session.get(NameRecipe, leaf)
    if not row:
        row = NameRecipe(leaf_id=leaf)
        session.add(row)
    row.style_json = json.dumps(style, ensure_ascii=False)
    row.updated_at = _dt.datetime.now(_dt.timezone.utc)
    await session.commit()
    await log_action(session, actor, "intake.recipe", target=leaf or "global", detail=json.dumps(style))
    return {"ok": True, "leaf": leaf, "style": style}


@router.post("/enrich", response_model=EnrichDraft)
def enrich(req: EnrichRequest, user=Depends(current_active_user)):
    if not req.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дай име на продукта.")
    import run_one
    t0 = time.time()
    cats = run_one.known_categories()
    try:
        text, model = run_one.generate_with_chain(_prompt(req.name, req.hint, cats))
    except Exception as e:
        # every free model drained / blocked / quota error
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Обогатяването е недостъпно в момента (безплатните квоти): {str(e)[:140]}")
    parsed = run_one.extract_json(text)
    if not parsed:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Моделът не върна валиден JSON — опитай пак.")

    def fix(s: str) -> str:  # apply_ru_fix returns (text, changed) — take the text
        r = run_one.apply_ru_fix(s or "")
        return r[0] if isinstance(r, tuple) else r
    attrs = []
    for a in (parsed.get("attributes") or []):
        if isinstance(a, dict) and a.get("key"):
            attrs.append(Attribute(key=fix(str(a.get("key", ""))),
                                   value=fix(str(a.get("value", "")))))
    clean_name = fix(str(parsed.get("name", req.name)))
    clean_desc = fix(str(parsed.get("description", "")))
    # meta fields: use the model's if given, else sensible SEO defaults (name / first ~155 chars)
    meta_title = fix(str(parsed.get("meta_title", ""))) or clean_name[:60]
    meta_description = fix(str(parsed.get("meta_description", ""))) or clean_desc[:155]
    return EnrichDraft(
        category=str(parsed.get("category", "")),
        name=clean_name,
        attributes=attrs,
        description=clean_desc,
        meta_title=meta_title,
        meta_description=meta_description,
        model=model,
        elapsed_ms=int((time.time() - t0) * 1000),
    )


# ---------------------------------------------------- approve → persist → push to Barsy ---
class ApproveRequest(BaseModel):
    # operational fields (human — never AI; price = Microinvest, per policy)
    code: str = ""
    barcode: str = ""
    price: str = ""
    unit: str = "бр"
    # enriched (approved) fields
    name: str
    category: str = ""
    shopify_category_id: str = ""
    brand: str = ""
    description: str = ""
    attributes: list[Attribute] = []
    meta_title: str = ""
    meta_description: str = ""
    model: str = ""


class DraftOut(BaseModel):
    id: int
    code: str
    barcode: str
    price: str
    unit: str
    name: str
    category: str
    shopify_category_id: str = ""
    brand: str = ""
    description: str
    attributes: list[Attribute]
    meta_title: str = ""
    meta_description: str = ""
    status: str
    barsy_article_id: int | None
    approved_by: str
    delivery_line_id: int | None = None
    completed: bool = False
    image_count: int = 0


# a delivery product counts as "done" (checkmarked, awaiting the delivery's finalise) once it's been
# enriched + saved, or already created in Barsy.
_DONE_STATES = {"done_from_delivery", "approved", "pushed"}


def _row_to_out(d: IntakeDraft, image_count: int = 0) -> DraftOut:
    return DraftOut(
        id=d.id, code=d.code, barcode=d.barcode, price=d.price, unit=d.unit,
        name=d.name, category=d.category,
        shopify_category_id=getattr(d, "shopify_category_id", "") or "",
        brand=getattr(d, "brand", "") or "",
        description=d.description,
        attributes=[Attribute(**a) for a in json.loads(d.attributes_json or "[]")],
        meta_title=getattr(d, "meta_title", "") or "", meta_description=getattr(d, "meta_description", "") or "",
        status=d.status, barsy_article_id=d.barsy_article_id, approved_by=d.approved_by,
        delivery_line_id=d.delivery_line_id,
        completed=(d.status in _DONE_STATES or d.barsy_article_id is not None),
        image_count=image_count)


async def _image_counts(session: AsyncSession, draft_ids: list[int]) -> dict[int, int]:
    """{draft_id -> #images} for a set of drafts (one grouped query)."""
    if not draft_ids:
        return {}
    rows = (await session.execute(
        select(DraftImage.draft_id, safunc.count()).where(DraftImage.draft_id.in_(draft_ids))
        .group_by(DraftImage.draft_id))).all()
    return {did: n for did, n in rows}


async def _upload_pending_images(session: AsyncSession, draft_id: int, article_id: int) -> int:
    """Upload every not-yet-uploaded DraftImage to the Barsy article (Files_upload). Best-effort per
    image; returns how many landed."""
    imgs = (await session.execute(select(DraftImage).where(
        DraftImage.draft_id == draft_id, DraftImage.uploaded_barsy.is_(False)))).scalars().all()
    n = 0
    for im in imgs:
        try:
            await run_in_threadpool(barsy.upload_image, article_id, im.data, im.filename)
            im.uploaded_barsy = True
            n += 1
        except Exception:
            pass   # leave it un-uploaded → retried on the next save
    await session.commit()
    return n


async def _save_to_barsy(session: AsyncSession, d: IntakeDraft, actor: User) -> dict:
    """Auto-create the saved product in Barsy (article + images) and, for a delivery product, send the
    delivery's storeload once ALL its new products are created. Real writes are gated by
    DELIVERY_BARSY_LIVE — pre-launch this DRY-RUNS (writes nothing) so review clicks can't create junk."""
    if not getattr(get_settings(), "delivery_barsy_live", False):
        return {"live": False, "created": False,
                "note": "Проба — не е записано в Barsy (ще се активира при пускане)."}
    art_body = _build_barsy_article(d)                       # {"article": {...}}
    if d.barsy_article_id:                                    # re-edit → update + upload any new images
        try:
            await run_in_threadpool(barsy.call, "Articles_update",
                                    {"article_id": d.barsy_article_id, "article": art_body["article"]})
        except Exception as e:
            return {"live": True, "created": True, "error": f"Barsy update: {str(e)[:140]}"}
        up = await _upload_pending_images(session, d.id, d.barsy_article_id)
        return {"live": True, "created": True, "article_id": d.barsy_article_id, "images_uploaded": up}
    try:                                                     # first save → create the article
        out = await run_in_threadpool(barsy.call, "Articles_create", art_body)
    except Exception as e:
        return {"live": True, "created": False, "error": f"Barsy create: {str(e)[:140]}"}
    aid = _extract_article_id(out)
    if aid is None:
        return {"live": True, "created": False, "error": "Barsy не върна article_id."}
    d.barsy_article_id = aid
    d.code = d.code or _extract_code(out)          # Barsy assigns + returns the SKU
    d.status = "pushed"
    d.pushed_at = _dt.datetime.now(_dt.timezone.utc)
    up = await _upload_pending_images(session, d.id, aid)
    note = {"live": True, "created": True, "article_id": aid, "code": d.code, "images_uploaded": up}
    if d.delivery_line_id:                                    # write id back + try auto-finalise
        line = await session.get(DeliveryLine, d.delivery_line_id)
        if line:
            line.article_id = aid
            line.is_new = False
        await session.commit()
        from .deliveries import autofinalise_if_ready
        note["delivery"] = await autofinalise_if_ready(session, line.batch_id if line else None, actor)
    else:
        await session.commit()
    return note


@router.post("/approve", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
async def approve(
    req: ApproveRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Persist an approved draft (panel-owned staging). Safe — no ERP write here."""
    d = IntakeDraft(
        code=req.code.strip(), barcode=req.barcode.strip(), price=req.price.strip(),
        unit=req.unit or "бр", name=req.name.strip(), category=req.category,
        shopify_category_id=req.shopify_category_id.strip(), brand=req.brand.strip(),
        description=req.description,
        attributes_json=json.dumps([a.model_dump() for a in req.attributes], ensure_ascii=False),
        meta_title=req.meta_title.strip(), meta_description=req.meta_description.strip(),
        model=req.model, status="approved", approved_by=actor.email)
    session.add(d)
    await session.commit()
    await session.refresh(d)
    await log_action(session, actor, "intake.approve", target=d.name, detail=f"code={d.code}")
    return _row_to_out(d)


@router.get("/drafts")
async def list_drafts(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    # STANDALONE drafts only (manually-created products). Delivery-sourced products live in their own
    # grouped view (/from-delivery) and must not double-show here.
    rows = (await session.execute(
        select(IntakeDraft).where(IntakeDraft.status.not_in(("new_from_delivery", "editing")),
                                  IntakeDraft.delivery_line_id.is_(None))
        .order_by(IntakeDraft.created_at.desc()).limit(100))).scalars().all()
    counts = await _image_counts(session, [d.id for d in rows])
    return {"drafts": [_row_to_out(d, counts.get(d.id, 0)) for d in rows]}


@router.get("/from-delivery")
async def from_delivery(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Products that arrived on a not-yet-finalised delivery. Includes BOTH the ones still to enrich
    AND the ones already done (checkmarked) — the whole delivery's products stay listed together and
    leave together only when the delivery is finalised (its storeload is sent). Each item carries its
    `delivery` context so the UI can group by delivery."""
    stmt = (select(IntakeDraft, DeliveryLine.batch_id, DeliveryBatch.supplier,
                   DeliveryBatch.reference, DeliveryBatch.doc_date, DeliveryBatch.status)
            .join(DeliveryLine, DeliveryLine.id == IntakeDraft.delivery_line_id)
            .join(DeliveryBatch, DeliveryBatch.id == DeliveryLine.batch_id)
            .where(IntakeDraft.delivery_line_id.is_not(None),
                   DeliveryBatch.status != "finalised")
            .order_by(DeliveryBatch.created_at.desc(), IntakeDraft.created_at.asc())
            .limit(500))
    rows = (await session.execute(stmt)).all()
    counts = await _image_counts(session, [d.id for d, *_ in rows])
    out = []
    for d, batch_id, supplier, reference, doc_date, bstatus in rows:
        item = _row_to_out(d, counts.get(d.id, 0)).model_dump()
        item["delivery"] = {"batch_id": batch_id, "supplier": supplier or "",
                            "reference": reference or "", "doc_date": doc_date or "", "status": bstatus}
        out.append(item)
    return {"drafts": out}


@router.put("/drafts/{draft_id}")
async def update_draft(
    draft_id: int, req: ApproveRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Save an EXISTING draft (enrich + operational fields). Requires our code and AT LEAST ONE IMAGE
    (mandatory). A delivery draft keeps its delivery_line_id. This is the 'Save' the owner asked to
    auto-create in Barsy — the ERP write + image upload + auto-finalise are wired in _save_to_barsy,
    gated by DELIVERY_BARSY_LIVE (dry-run pre-launch)."""
    d = await session.get(IntakeDraft, draft_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Черновата не е намерена.")
    if d.status == "pushed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Вече е създадена в Barsy.")
    # NB: no 'code' requirement — Barsy assigns the SKU on create and returns it (see _save_to_barsy).
    n_images = (await _image_counts(session, [draft_id])).get(draft_id, 0)
    if n_images == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Задължителна е поне една снимка на продукта.")
    d.code = req.code.strip() or d.code
    d.barcode = req.barcode.strip() or d.barcode
    d.price = req.price.strip()
    d.unit = req.unit or "бр"
    d.name = req.name.strip()
    d.category = req.category
    d.shopify_category_id = req.shopify_category_id.strip()
    d.brand = req.brand.strip()
    d.description = req.description
    d.attributes_json = json.dumps([a.model_dump() for a in req.attributes], ensure_ascii=False)
    d.meta_title = req.meta_title.strip()
    d.meta_description = req.meta_description.strip()
    d.model = req.model
    # A delivery-sourced draft stays linked to its delivery and remains in the Нов продукт queue
    # (checkmarked) until the whole delivery is finalised — so the batch disappears together, not
    # one-by-one. A standalone draft (no delivery line) just becomes 'approved'.
    d.status = "done_from_delivery" if d.delivery_line_id else "approved"
    d.approved_by = actor.email
    await session.commit()
    await session.refresh(d)
    # Save AUTO-CREATES in Barsy (article + images), and auto-sends the delivery's storeload once all
    # its new products are created. Real writes are gated by DELIVERY_BARSY_LIVE (dry-run pre-launch).
    barsy_note = await _save_to_barsy(session, d, actor)
    await log_action(session, actor, "intake.update", target=d.name, detail=f"code={d.code}")
    out = _row_to_out(d, n_images).model_dump()
    out["barsy"] = barsy_note
    return out


# ---------------------------------------------------------------- product images (mandatory) ---
_IMG_MAX = 8 * 1024 * 1024
_IMG_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


@router.post("/drafts/blank", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
async def create_blank_draft(actor: User = Depends(current_active_user),
                             session: AsyncSession = Depends(get_async_session)):
    """Start a NEW standalone product (not from a delivery) — an empty draft so the same
    barcode+images-first editor can attach images + save it."""
    d = IntakeDraft(status="editing", approved_by=actor.email)
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return _row_to_out(d, 0)


@router.post("/drafts/{draft_id}/images", status_code=status.HTTP_201_CREATED)
async def add_images(draft_id: int, files: list[UploadFile] = File(...),
                     actor: User = Depends(current_active_user),
                     session: AsyncSession = Depends(get_async_session)):
    """Attach image(s) to a draft — stored in admin.db until the article is created, then uploaded to
    Barsy (Files_upload) on save."""
    d = await session.get(IntakeDraft, draft_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Черновата не е намерена.")
    saved = []
    for f in files:
        raw = await f.read(_IMG_MAX + 1)
        if not raw:
            continue
        if len(raw) > _IMG_MAX:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Снимката е твърде голяма (макс. 8 MB).")
        ct = (f.content_type or "image/jpeg").lower()
        if ct not in _IMG_EXT:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Поддържани формати: JPG, PNG, WEBP, GIF.")
        fn = f.filename or ("image" + _IMG_EXT[ct])
        if not fn.lower().endswith(tuple(_IMG_EXT.values())):
            fn = fn + _IMG_EXT[ct]     # Barsy Files_upload needs a real image extension
        img = DraftImage(draft_id=draft_id, filename=fn[:255], content_type=ct, data=raw)
        session.add(img)
        await session.flush()
        saved.append({"id": img.id, "filename": img.filename})
    await session.commit()
    n = (await _image_counts(session, [draft_id])).get(draft_id, 0)
    return {"added": saved, "image_count": n}


@router.get("/drafts/{draft_id}/images")
async def list_images(draft_id: int, actor: User = Depends(current_active_user),
                      session: AsyncSession = Depends(get_async_session)):
    rows = (await session.execute(
        select(DraftImage.id, DraftImage.filename, DraftImage.uploaded_barsy)
        .where(DraftImage.draft_id == draft_id).order_by(DraftImage.id))).all()
    return {"images": [{"id": i, "filename": fn, "uploaded": bool(u)} for i, fn, u in rows]}


@router.get("/drafts/{draft_id}/images/{image_id}")
async def get_image(draft_id: int, image_id: int, actor: User = Depends(current_active_user),
                    session: AsyncSession = Depends(get_async_session)):
    im = await session.get(DraftImage, image_id)
    if not im or im.draft_id != draft_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава снимка.")
    return Response(content=im.data, media_type=im.content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.delete("/drafts/{draft_id}/images/{image_id}")
async def delete_image(draft_id: int, image_id: int, actor: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)):
    im = await session.get(DraftImage, image_id)
    if not im or im.draft_id != draft_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава снимка.")
    await session.delete(im)
    await session.commit()
    n = (await _image_counts(session, [draft_id])).get(draft_id, 0)
    return {"ok": True, "image_count": n}


def _build_barsy_article(d: IntakeDraft) -> dict:
    """Map an approved draft to a Barsy Articles_create body (verified contract). The article `code`
    (our SKU) is assigned BY BARSY on create — we omit it so Barsy generates it and returns it. A code
    is only sent when we already have one (e.g. a delivery-matched existing product)."""
    art: dict = {
        "article_name": d.name,
        "article_name_ml": {"bg_BG": d.name},
        "amount_type_id": _UNIT_ID.get(d.unit, 1),
        "is_public": 1, "is_for_sale": 1,
    }
    if d.code:
        art["code"] = d.code
    # NOTE: shopify_category_id is NOT sent to Barsy (unknown field) — it rides to Shopify via the
    # connector/metafields at mirror time; it's persisted on the draft.
    if d.description:
        art["description_ml"] = {"bg_BG": d.description}
    if d.barcode:
        art["barcode"] = d.barcode  # Barsy create rejects the array form (500) — send string
    if d.price:
        art["actual_price"] = str(d.price)  # euros; staff-entered, never AI (price policy)
    attrs = json.loads(d.attributes_json or "[]")
    if attrs:
        art["details"] = [{"detail_name": a["key"], "detail_value": a["value"]}
                          for a in attrs if a.get("key")]
    return {"article": art}


class PushRequest(BaseModel):
    confirm: bool = False


def _extract_article_id(out):
    """Articles_create may return a bare int OR a dict with article_id/id (mirrors
    barsy_push.py). Returns the id, or None if the response can't be parsed."""
    def _as_int(v):
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return None
    if isinstance(out, dict):
        return _as_int(out.get("article_id")) or _as_int(out.get("id"))
    return _as_int(out)


def _extract_code(out) -> str:
    """The SKU Barsy assigned on create (returned in the response) — so we don't ask staff for it."""
    if isinstance(out, dict):
        for k in ("code", "article_code"):
            v = out.get(k)
            if v not in (None, ""):
                return str(v)
        art = out.get("article")
        if isinstance(art, dict) and art.get("code"):
            return str(art["code"])
    return ""


@router.post("/drafts/{draft_id}/push-barsy")
async def push_to_barsy(
    draft_id: int, req: PushRequest,
    actor: User = Depends(current_owner),  # ERP write — owner only
    session: AsyncSession = Depends(get_async_session),
):
    """Create the approved product as a Barsy article. Real ERP write: owner-only,
    needs confirm=True, audit-logged, and refuses to double-push."""
    d = await session.get(IntakeDraft, draft_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Черновата не е намерена.")
    # no 'code' requirement — Barsy assigns the SKU on create and returns it (captured below)
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Създаването в Barsy е реално действие — потвърди с confirm=true.")

    # Atomic claim: only the request that flips a ready draft → pushing proceeds. Blocks the
    # double-push race (two owners / double-click) BEFORE the real ERP write. A delivery-sourced
    # draft is 'done_from_delivery' (enriched, checkmarked in the queue); a standalone one is 'approved'.
    claim = await session.execute(
        update(IntakeDraft).where(IntakeDraft.id == draft_id,
                                  IntakeDraft.status.in_(("approved", "done_from_delivery")))
        .values(status="pushing"))
    await session.commit()
    if claim.rowcount == 0:
        await session.refresh(d)
        if d.status == "pushed":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Вече е създадена в Barsy (article_id {d.barsy_article_id}).")
        raise HTTPException(status.HTTP_409_CONFLICT, "Създаването вече тече.")

    try:
        out = await run_in_threadpool(barsy.call, "Articles_create", _build_barsy_article(d))
    except Exception as e:
        d.status = "approved"  # release the claim so it can be retried
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {str(e)[:180]}")

    # The write has already happened. Mark pushed even if the id can't be parsed, so a
    # retry can't create a duplicate; surface a warning to verify in Barsy.
    new_id = _extract_article_id(out)
    d.status = "pushed"
    d.barsy_article_id = new_id
    d.code = d.code or _extract_code(out)          # Barsy-assigned SKU
    d.pushed_at = _dt.datetime.now(_dt.timezone.utc)
    # if this product arrived on a delivery, reconcile its line so the delivery's finalise gate can
    # clear. Mark it existing + carry our code as match_code EVEN IF new_id couldn't be parsed —
    # the article WAS created in Barsy, so finalise can resolve it by code instead of stranding the
    # line as a forever-"new" product with no article_id.
    if d.delivery_line_id:
        from ..models import DeliveryLine
        line = await session.get(DeliveryLine, d.delivery_line_id)
        if line and line.article_id is None:
            line.is_new = False
            line.match_code = line.match_code or d.code
            if new_id is not None:
                line.article_id = new_id
    await session.commit()
    await log_action(session, actor, "intake.push_barsy", target=d.name,
                     detail=f"code={d.code} article_id={new_id}")
    resp = {"ok": True, "barsy_article_id": new_id, "status": "pushed"}
    if new_id is None:
        resp["warning"] = ("Създадена в Barsy, но ID не се разчете от отговора — "
                           "провери артикула в Barsy.")
    return resp
