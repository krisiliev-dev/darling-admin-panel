# -*- coding: utf-8 -*-
"""Supplier links reach Barsy's real model (Articles_save_suppliers) — task #77.

These assert the BODY sent to Barsy, because that body is the link. The contract they lock down
was live-captured on 2026-08-04 (docs/BARSY_API_MAP_20260804.md): rows carry the supplier's own
code and name; the save is full-replace, so an update must restate what it doesn't own.
"""
import pytest
from sqlalchemy import select

BARSY_SUPPLIERS = [
    {"supplier_id": 39, "supplier_name": "МИРА-Н-НАДКА ВАСИЛЕВА", "delete_flag": 0},
    {"supplier_id": 41, "supplier_name": '"ЛИНЕКС" ЕООД', "delete_flag": 0},
    {"supplier_id": 44, "supplier_name": "Изтрит Доставчик", "delete_flag": 1},
]


async def _draft_with_suppliers(make_draft, rows):
    from app.db import async_session_maker
    from app.models import DraftSupplier
    did = await make_draft(name="Тест чиния", price="1.00", status="draft")
    async with async_session_maker() as s:
        for r in rows:
            s.add(DraftSupplier(draft_id=did, **r))
        await s.commit()
    return did


def _links_calls(rec):
    return [b for (op, b) in rec.calls if op == "Articles_save_suppliers"]


@pytest.mark.asyncio
async def test_create_pushes_all_supplier_rows_as_links(owner_client, make_draft, barsy_recorder, monkeypatch):
    from app.routers import intake as im
    monkeypatch.setattr(im, "get_settings", lambda: type("S", (), {
        "delivery_barsy_live": True, "barsy_min_profit_percent": 0})())
    barsy_recorder.set_default(None)
    barsy_recorder.set_return("Articles_create", {"article_id": 777, "code": "SKU777"})
    barsy_recorder.set_return("Suppliers_getlist", BARSY_SUPPLIERS)

    did = await _draft_with_suppliers(make_draft, [
        {"supplier": "Мира-Н", "supplier_code": "MIRA-77", "supplier_name": "ЧИНИЯ ПРИ МИРА"},
        {"supplier": "Линекс", "supplier_code": "LX-9", "supplier_name": ""},
    ])
    from app.db import async_session_maker
    from app.models import IntakeDraft, User
    from app.routers.intake import _save_to_barsy
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        actor = (await s.execute(select(User))).scalars().first()
        note = await _save_to_barsy(s, d, actor)

    assert note.get("article_id") == 777, note
    assert note.get("suppliers_linked") == 2
    calls = _links_calls(barsy_recorder)
    assert len(calls) == 1
    body = calls[0]
    # the captured contract, field for field
    assert body["id"] == 777 and body["action_type"] == "save" and body["values"] == {}
    rows = body["rows"]
    assert [r["supplier_id"] for r in rows] == [39, 41]
    assert rows[0]["remote_ref_id"] == "MIRA-77"
    assert rows[0]["remote_article_name"] == "ЧИНИЯ ПРИ МИРА"
    assert rows[0]["supplier_name"] == "МИРА-Н-НАДКА ВАСИЛЕВА"   # Barsy's canonical, not ours
    assert rows[0]["is_default"] == 1 and rows[1]["is_default"] == 0
    # names no longer leak into article_aliases — they have a real home now
    create = next(b for (op, b) in barsy_recorder.calls if op == "Articles_create")
    assert "ЧИНИЯ ПРИ МИРА" not in (create["article"].get("article_aliases") or [])


@pytest.mark.asyncio
async def test_an_unresolvable_supplier_is_skipped_and_named_never_invented(owner_client, make_draft, barsy_recorder, monkeypatch):
    from app.routers import intake as im
    monkeypatch.setattr(im, "get_settings", lambda: type("S", (), {
        "delivery_barsy_live": True, "barsy_min_profit_percent": 0})())
    barsy_recorder.set_default(None)
    barsy_recorder.set_return("Articles_create", {"article_id": 778, "code": "SKU778"})
    barsy_recorder.set_return("Suppliers_getlist", BARSY_SUPPLIERS)

    did = await _draft_with_suppliers(make_draft, [
        {"supplier": "Мира-Н", "supplier_code": "M-1", "supplier_name": ""},
        {"supplier": "Несъществуващ Доставчик", "supplier_code": "X-2", "supplier_name": ""},
    ])
    from app.db import async_session_maker
    from app.models import IntakeDraft, User
    from app.routers.intake import _save_to_barsy
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        actor = (await s.execute(select(User))).scalars().first()
        note = await _save_to_barsy(s, d, actor)

    assert note.get("suppliers_linked") == 1
    assert note.get("suppliers_skipped") == ["Несъществуващ Доставчик"]
    rows = _links_calls(barsy_recorder)[0]["rows"]
    assert [r["supplier_id"] for r in rows] == [39]
    # the product save itself succeeded — supplier metadata must never sink it
    assert note.get("created") is True


@pytest.mark.asyncio
async def test_update_keeps_links_it_is_not_restating_full_replace_rule(owner_client, make_draft, barsy_recorder, monkeypatch):
    """Articles_save_suppliers is full-replace: an update that only knows OUR rows must first
    read the existing links and restate the ones a manager added in Barsy directly."""
    from app.routers import intake as im
    monkeypatch.setattr(im, "get_settings", lambda: type("S", (), {
        "delivery_barsy_live": True, "barsy_min_profit_percent": 0})())
    barsy_recorder.set_default(None)
    barsy_recorder.set_return("Suppliers_getlist", BARSY_SUPPLIERS)
    barsy_recorder.set_return("Articles_update", 1)
    barsy_recorder.set_return("articles_suppliers", {"rows": [
        {"supplier_id": 41, "supplier_name": '"ЛИНЕКС" ЕООД', "is_default": 1,
         "default_delivery_price": "2.50", "package_id": "", "remote_ref_id": "LX-OLD",
         "remote_article_name": "СТАРО ИМЕ"},
    ]})

    did = await _draft_with_suppliers(make_draft, [
        {"supplier": "Мира-Н", "supplier_code": "M-9", "supplier_name": "НОВО ПРИ МИРА"},
    ])
    from app.db import async_session_maker
    from app.models import IntakeDraft, User
    from app.routers.intake import _save_to_barsy
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        d.barsy_article_id = 900
        await s.commit()
        actor = (await s.execute(select(User))).scalars().first()
        note = await _save_to_barsy(s, d, actor)

    assert note.get("suppliers_linked") == 1, note
    rows = _links_calls(barsy_recorder)[0]["rows"]
    ids = [r["supplier_id"] for r in rows]
    assert ids == [41, 39], "the manager's Линекс link must survive our restatement"
    linex = rows[0]
    assert linex["default_delivery_price"] == "2.50" and linex["remote_ref_id"] == "LX-OLD"
    assert linex["is_default"] == 1
    mira = rows[1]
    assert mira["is_default"] == 0, "an existing основен must not be stolen"


@pytest.mark.asyncio
async def test_no_supplier_rows_means_no_links_call(owner_client, make_draft, barsy_recorder, monkeypatch):
    from app.routers import intake as im
    monkeypatch.setattr(im, "get_settings", lambda: type("S", (), {
        "delivery_barsy_live": True, "barsy_min_profit_percent": 0})())
    barsy_recorder.set_default(None)
    barsy_recorder.set_return("Articles_create", {"article_id": 780, "code": "SKU780"})

    did = await make_draft(name="Без доставчици", price="1.00", status="draft")
    from app.db import async_session_maker
    from app.models import IntakeDraft, User
    from app.routers.intake import _save_to_barsy
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        actor = (await s.execute(select(User))).scalars().first()
        note = await _save_to_barsy(s, d, actor)
    assert note.get("created") is True
    assert _links_calls(barsy_recorder) == []


@pytest.mark.asyncio
async def test_a_links_failure_never_sinks_the_product_save(owner_client, make_draft, barsy_recorder, monkeypatch):
    from app.routers import intake as im
    monkeypatch.setattr(im, "get_settings", lambda: type("S", (), {
        "delivery_barsy_live": True, "barsy_min_profit_percent": 0})())
    barsy_recorder.set_default(None)
    barsy_recorder.set_return("Articles_create", {"article_id": 781, "code": "SKU781"})
    barsy_recorder.set_return("Suppliers_getlist", BARSY_SUPPLIERS)

    def _boom(body):
        raise RuntimeError("Barsy hiccup")
    barsy_recorder.set_return("Articles_save_suppliers", _boom)

    did = await _draft_with_suppliers(make_draft, [
        {"supplier": "Мира-Н", "supplier_code": "M-1", "supplier_name": ""},
    ])
    from app.db import async_session_maker
    from app.models import IntakeDraft, User
    from app.routers.intake import _save_to_barsy
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        actor = (await s.execute(select(User))).scalars().first()
        note = await _save_to_barsy(s, d, actor)
    assert note.get("created") is True and note.get("article_id") == 781
    assert note.get("suppliers_linked") == 0
    assert "Articles_save_suppliers" in note.get("suppliers_note", "")
