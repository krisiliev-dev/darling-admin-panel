# -*- coding: utf-8 -*-
"""Product intake — the signature module. Turns a bare stock name (and, later, a photo)
into a draft: category → attributes → clean name → description. The AI draft is the panel's
'pencil' — grey and overwriteable; the human commits in 'pen'.

Enrichment reuses the pipeline's FREE-QUOTA, guarded model chain (generate_with_chain →
run_direct, which honours the NEVER_CALL blocklist, exhausted-model list and DashScope
opt-in). It does NOT touch the live fleet's task/shard/telemetry-mutation machinery — it's a
single, isolated, low-volume call per product."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..audit import log_action
from ..core import barsy
from ..db import get_async_session
from ..models import DeliveryBatch, DeliveryLine, IntakeDraft, User
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
    description: str
    attributes: list[Attribute]
    meta_title: str = ""
    meta_description: str = ""
    status: str
    barsy_article_id: int | None
    approved_by: str
    delivery_line_id: int | None = None
    completed: bool = False


# a delivery product counts as "done" (checkmarked, awaiting the delivery's finalise) once it's been
# enriched + saved, or already created in Barsy.
_DONE_STATES = {"done_from_delivery", "approved", "pushed"}


def _row_to_out(d: IntakeDraft) -> DraftOut:
    return DraftOut(
        id=d.id, code=d.code, barcode=d.barcode, price=d.price, unit=d.unit,
        name=d.name, category=d.category, description=d.description,
        attributes=[Attribute(**a) for a in json.loads(d.attributes_json or "[]")],
        meta_title=getattr(d, "meta_title", "") or "", meta_description=getattr(d, "meta_description", "") or "",
        status=d.status, barsy_article_id=d.barsy_article_id, approved_by=d.approved_by,
        delivery_line_id=d.delivery_line_id,
        completed=(d.status in _DONE_STATES or d.barsy_article_id is not None))


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
        select(IntakeDraft).where(IntakeDraft.status != "new_from_delivery",
                                  IntakeDraft.delivery_line_id.is_(None))
        .order_by(IntakeDraft.created_at.desc()).limit(100))).scalars().all()
    return {"drafts": [_row_to_out(d) for d in rows]}


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
    out = []
    for d, batch_id, supplier, reference, doc_date, bstatus in rows:
        item = _row_to_out(d).model_dump()
        item["delivery"] = {"batch_id": batch_id, "supplier": supplier or "",
                            "reference": reference or "", "doc_date": doc_date or "", "status": bstatus}
        out.append(item)
    return {"drafts": out}


@router.put("/drafts/{draft_id}", response_model=DraftOut)
async def update_draft(
    draft_id: int, req: ApproveRequest,
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Enrich + approve an EXISTING draft in place (used for products staged from a delivery, so the
    draft keeps its delivery_line_id → pushing it later writes the article_id back onto that line)."""
    d = await session.get(IntakeDraft, draft_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Черновата не е намерена.")
    if d.status == "pushed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Вече е създадена в Barsy.")
    d.code = req.code.strip() or d.code
    d.barcode = req.barcode.strip() or d.barcode
    d.price = req.price.strip()
    d.unit = req.unit or "бр"
    d.name = req.name.strip()
    d.category = req.category
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
    await log_action(session, actor, "intake.update", target=d.name, detail=f"code={d.code}")
    return _row_to_out(d)


def _build_barsy_article(d: IntakeDraft) -> dict:
    """Map an approved draft to a Barsy Articles_create body (verified contract)."""
    art: dict = {
        "code": d.code,
        "article_name": d.name,
        "article_name_ml": {"bg_BG": d.name},
        "amount_type_id": _UNIT_ID.get(d.unit, 1),
        "is_public": 1, "is_for_sale": 1,
    }
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
    if not d.code.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Липсва код (бизнес ключ) — задай го преди създаване в Barsy.")
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
    import datetime as _dt
    d.status = "pushed"
    d.barsy_article_id = new_id
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
