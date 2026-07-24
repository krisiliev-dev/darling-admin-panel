# -*- coding: utf-8 -*-
"""Асистент — the in-panel command box. Staff ask in plain Bulgarian; Claude answers, grounded
in a LIVE snapshot of the panel's real numbers (hallucination mitigation = grounding). Read-only
v1: it explains, reports, and points at the right module — it does not perform actions yet.

Uses the pipeline's FREE-QUOTA guarded model chain (generate_with_chain), isolated from the fleet."""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .. import assistant_llm
from ..core import analytics, db
from ..db import get_async_session
from ..models import IntakeDraft, User
from ..users import current_active_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

SYSTEM = (
    "Ти си асистентът на вътрешния административен панел на Дарлинг (магазин за домашни стоки). "
    "Отговаряй кратко и на чист български. Използвай САМО данните по-долу — това са реалните "
    "числа от панела в момента. Ако нещо липсва в данните, кажи честно, че нямаш тази информация, "
    "и насочи към подходящия модул. НЕ измисляй числа и НЕ извършвай действия — само обяснявай, "
    "обобщавай и насочвай.\n"
    "Модули на панела: Анализи (числа/готовност на каталога), Приемане (нови продукти + "
    "обогатяване с AI), Поръчки (изпълнение + етикети от Спиди/Еконт с наложен платеж), "
    "Разчети (наложен платеж — събрано/изплатено), Наличности (запаси от Barsy), Асистент (това)."
)


def _one(c, q, default=0):
    try:
        v = c.execute(q).fetchone()
        return (v[0] if v and v[0] is not None else default)
    except Exception:
        return default


def _local_snapshot() -> dict:
    """The sync (sqlite + analytics) part of the snapshot — run in a threadpool."""
    c = db.connect()
    try:
        catalog = {
            "продукти_общо": _one(c, "SELECT COUNT(*) FROM products"),
            "готови_за_старт": _one(c, "SELECT COUNT(*) FROM launch_readiness WHERE ready=1"),
            "блокирани": _one(c, "SELECT COUNT(*) FROM launch_readiness WHERE ready=0"),
            "искат_човек": _one(c, "SELECT SUM(needs_human) FROM products"),
            "доставчици": _one(c, "SELECT COUNT(DISTINCT supplier) FROM dim_product WHERE supplier<>''"),
        }
        # fulfillment / COD straight from fulfillment_state (fast, self-contained)
        fstatus = {}
        try:
            for stt, n in c.execute("SELECT status, COUNT(*) FROM fulfillment_state GROUP BY status").fetchall():
                fstatus[stt or "new"] = n
        except Exception:
            pass
        cod = {"в_обращение": 0.0, "за_изплащане": 0.0}
        try:
            rows = c.execute("SELECT status, SUM(cod_amount) FROM fulfillment_state "
                             "WHERE cod_amount>0 GROUP BY status").fetchall()
            for stt, amt in rows:
                if stt == "shipped":
                    cod["в_обращение"] = round(amt or 0, 2)
                elif stt == "delivered":
                    cod["за_изплащане"] = round(amt or 0, 2)
        except Exception:
            pass
    finally:
        c.close()

    try:
        kpis = analytics.kpis()
    except Exception:
        kpis = {}
    return {"catalog": catalog, "fstatus": fstatus, "cod": cod, "kpis": kpis}


async def _snapshot(session: AsyncSession) -> dict:
    """A compact, FAST live snapshot to ground the answer. Sync sqlite/analytics run in a
    threadpool so the async handler doesn't block the event loop."""
    loc = await run_in_threadpool(_local_snapshot)
    kpis = loc["kpis"]
    drafts_pending = (await session.execute(
        select(func.count()).select_from(IntakeDraft).where(IntakeDraft.status == "approved"))).scalar_one()
    drafts_pushed = (await session.execute(
        select(func.count()).select_from(IntakeDraft).where(IntakeDraft.status == "pushed"))).scalar_one()
    return {
        "каталог": loc["catalog"],
        "продажби": {"поръчки_общо": kpis.get("orders_total", 0),
                     "приход_общо_€": kpis.get("revenue_total", 0),
                     "средна_цена_€": kpis.get("avg_retail", 0)},
        "изпълнение_по_статус": loc["fstatus"] or "няма поръчки още",
        "наложен_платеж_€": loc["cod"],
        "чернови_за_създаване": drafts_pending,
        "създадени_в_barsy": drafts_pushed,
        "магазин_пуснат": bool(kpis.get("orders_total", 0)),
    }


class AskRequest(BaseModel):
    question: str


class Suggestion(BaseModel):
    kind: str          # "goto" (navigate) | "sync_tracking" (calls an endpoint)
    label: str
    to: str = ""       # route for goto


class AskResponse(BaseModel):
    answer: str
    model: str
    snapshot: dict
    suggestions: list[Suggestion] = []


# Deterministic suggested-actions from the question + live state. Safe: they only PROPOSE;
# the human clicks, and every target endpoint enforces its own role/confirm gating (so a wrong
# suggestion is harmless). Richer LLM tool-calling comes with the Claude backend.
_INTENTS = [
    (("проследяв", "проследи", "доставен", "доставка", "изпратен", "къде е"),
     Suggestion(kind="sync_tracking", label="Синхронизирай проследяване")),
    (("наличност", "запас", "нисък", "поръчай от доставчик", "свърши"),
     Suggestion(kind="goto", label="Виж наличности", to="/inventory")),
    (("чернов", "приемане", "нов продукт", "добави продукт", "обогат"),
     Suggestion(kind="goto", label="Отвори Приемане", to="/intake")),
    (("наложен платеж", "разчет", "изплащане", "събрано"),
     Suggestion(kind="goto", label="Виж Разчети", to="/cod")),
    (("поръчк", "етикет", "пратк", "спиди", "еконт"),
     Suggestion(kind="goto", label="Отвори Поръчки", to="/orders")),
]


def _suggest(question: str, snap: dict) -> list[Suggestion]:
    q = question.lower()
    out, seen = [], set()
    for keys, sug in _INTENTS:
        if any(k in q for k in keys) and sug.label not in seen:
            out.append(sug); seen.add(sug.label)
    return out[:2]


@router.post("/ask", response_model=AskResponse)
async def ask(
    req: AskRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not req.question.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Задай въпрос.")
    snap = await _snapshot(session)
    prompt = (f"ДАННИ В МОМЕНТА (JSON):\n{json.dumps(snap, ensure_ascii=False, indent=1)}\n\n"
              f"ВЪПРОС ОТ СЛУЖИТЕЛ: {req.question.strip()}\n\nОтговори кратко на български.")
    try:
        # blocking (subprocess / HTTP) → threadpool so the event loop stays free
        answer, model = await run_in_threadpool(assistant_llm.answer, SYSTEM, prompt)
    except Exception as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"Асистентът е недостъпен: {str(e)[:160]}")
    import run_one
    fixed = run_one.apply_ru_fix(answer)
    answer = fixed[0] if isinstance(fixed, tuple) else fixed
    return AskResponse(answer=answer.strip(), model=model, snapshot=snap,
                       suggestions=_suggest(req.question, snap))


@router.get("/backends")
def backends(user: User = Depends(current_active_user)):
    """Which model backends are active + ready (for the panel to display)."""
    return {"backends": assistant_llm.configured_backends()}
