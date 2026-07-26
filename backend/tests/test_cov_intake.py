# -*- coding: utf-8 -*-
"""Приемане (/api/intake) — the AI-draft pipeline endpoints OTHER than push-barsy (which is
already covered by test_push_guards / test_role_gating / test_build_barsy_article).

Covered here:
  * GET  /categories  — taxonomy passthrough
  * POST /enrich       — LLM draft mapping + 400 (empty) / 503 (chain drained) / 502 (bad JSON)
  * POST /approve      — persist an approved draft (no ERP write) + auth
  * GET  /drafts       — list persisted drafts

enrich/categories call the pipeline's guarded model chain via `import run_one`; we monkeypatch
run_one so no real LLM/quota is touched.
"""

import run_one


# --- GET /categories ---------------------------------------------------------------
async def test_categories_passthrough(staff_client, monkeypatch):
    monkeypatch.setattr(run_one, "known_categories", lambda: ["Свещи", "Съдове", "Текстил"])
    r = await staff_client.get("/api/intake/categories")
    assert r.status_code == 200, r.text
    assert r.json() == {"categories": ["Свещи", "Съдове", "Текстил"]}


async def test_categories_requires_auth(client):
    r = await client.get("/api/intake/categories")
    assert r.status_code == 401


# --- POST /enrich ------------------------------------------------------------------
def _stub_enrich(monkeypatch, parsed, model="qwen-flash"):
    monkeypatch.setattr(run_one, "known_categories", lambda: ["Свещи", "Съдове"])
    monkeypatch.setattr(run_one, "generate_with_chain", lambda prompt: ("<json>", model))
    monkeypatch.setattr(run_one, "extract_json", lambda text: parsed)
    # identity ru-fix (returns the (text, changed) tuple the handler unpacks)
    monkeypatch.setattr(run_one, "apply_ru_fix", lambda s: (s, False))


async def test_enrich_maps_model_output(staff_client, monkeypatch):
    _stub_enrich(monkeypatch, {
        "category": "Свещи",
        "name": "Ароматна свещ",
        "attributes": [{"key": "Материал", "value": "восък"},
                       {"key": "", "value": "ignored"}],   # keyless attr dropped
        "description": "Приятен аромат.",
    })
    r = await staff_client.post("/api/intake/enrich", json={"name": "свещ", "hint": "лавандула"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "Свещи"
    assert body["name"] == "Ароматна свещ"
    assert body["description"] == "Приятен аромат."
    assert body["model"] == "qwen-flash"
    assert isinstance(body["elapsed_ms"], int)
    # only the attribute with a key survives
    assert body["attributes"] == [{"key": "Материал", "value": "восък"}]


async def test_enrich_empty_name_is_400(staff_client, monkeypatch):
    _stub_enrich(monkeypatch, {})
    r = await staff_client.post("/api/intake/enrich", json={"name": "   "})
    assert r.status_code == 400


async def test_enrich_chain_drained_is_503(staff_client, monkeypatch):
    monkeypatch.setattr(run_one, "known_categories", lambda: [])

    def drained(prompt):
        raise RuntimeError("all free quotas exhausted")
    monkeypatch.setattr(run_one, "generate_with_chain", drained)
    r = await staff_client.post("/api/intake/enrich", json={"name": "свещ"})
    assert r.status_code == 503
    assert "безплатните квоти" in r.json()["detail"]


async def test_enrich_unparseable_json_is_502(staff_client, monkeypatch):
    monkeypatch.setattr(run_one, "known_categories", lambda: [])
    monkeypatch.setattr(run_one, "generate_with_chain", lambda prompt: ("not json", "m"))
    monkeypatch.setattr(run_one, "extract_json", lambda text: None)   # model returned junk
    r = await staff_client.post("/api/intake/enrich", json={"name": "свещ"})
    assert r.status_code == 502


async def test_enrich_requires_auth(client):
    r = await client.post("/api/intake/enrich", json={"name": "свещ"})
    assert r.status_code == 401


# --- POST /approve -----------------------------------------------------------------
async def test_approve_persists_draft(staff_client):
    r = await staff_client.post("/api/intake/approve", json={
        "code": " SKU-9 ", "barcode": " 3800130123456 ", "price": " 12.50 ",
        "unit": "бр", "name": "  Кана  ", "category": "Съдове",
        "description": "Керамична кана.",
        "attributes": [{"key": "Материал", "value": "керамика"}],
        "model": "qwen-flash",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] > 0
    assert body["status"] == "approved"
    assert body["code"] == "SKU-9"           # trimmed
    assert body["name"] == "Кана"            # trimmed
    assert body["barcode"] == "3800130123456"
    assert body["price"] == "12.50"
    assert body["barsy_article_id"] is None  # no ERP write on approve
    assert body["approved_by"] == "staff@example.com"
    assert body["attributes"] == [{"key": "Материал", "value": "керамика"}]

    # it is now listed in /drafts
    lst = await staff_client.get("/api/intake/drafts")
    assert lst.status_code == 200
    assert any(d["id"] == body["id"] for d in lst.json()["drafts"])


async def test_approve_defaults_unit_when_blank(staff_client):
    r = await staff_client.post("/api/intake/approve", json={"name": "Само име", "unit": ""})
    assert r.status_code == 201, r.text
    assert r.json()["unit"] == "бр"


async def test_approve_requires_name(staff_client):
    r = await staff_client.post("/api/intake/approve", json={"code": "X"})
    assert r.status_code == 422   # name is required by the schema


async def test_approve_requires_auth(client):
    r = await client.post("/api/intake/approve", json={"name": "Кана"})
    assert r.status_code == 401


# --- GET /drafts -------------------------------------------------------------------
async def test_drafts_empty_then_populated(owner_client, make_draft):
    r0 = await owner_client.get("/api/intake/drafts")
    assert r0.status_code == 200 and r0.json()["drafts"] == []
    await make_draft(code="A", name="Едно", status="approved")
    await make_draft(code="B", name="Две", status="pushed", barsy_article_id=42)
    r1 = await owner_client.get("/api/intake/drafts")
    drafts = r1.json()["drafts"]
    assert len(drafts) == 2
    assert {d["status"] for d in drafts} == {"approved", "pushed"}


async def test_drafts_requires_auth(client):
    r = await client.get("/api/intake/drafts")
    assert r.status_code == 401


# --- _save_to_barsy atomic claim: a double-submit must not create two Barsy articles ---
async def test_save_to_barsy_claim_blocks_concurrent_create(make_draft, monkeypatch, mock_barsy):
    from types import SimpleNamespace

    import app.routers.intake as intake
    from app.db import async_session_maker
    from app.models import IntakeDraft
    monkeypatch.setattr(intake, "get_settings", lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))
    # a draft mid-creation: barsy_article_id = -1 (the claim sentinel a concurrent save holds)
    did = await make_draft(code="X", name="Дубликат", barsy_article_id=-1, status="approved")
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        res = await intake._save_to_barsy(s, d, SimpleNamespace(email="o@x.bg", id="u"))
    assert res["created"] is False and "вече създава" in res["error"]
    assert "Articles_create" not in [op for op, _ in mock_barsy]   # no duplicate create


async def test_save_to_barsy_creates_once_and_claims(make_draft, monkeypatch, mock_barsy):
    from types import SimpleNamespace

    import app.routers.intake as intake
    from app.db import async_session_maker
    from app.models import IntakeDraft
    monkeypatch.setattr(intake, "get_settings", lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))
    did = await make_draft(code="Y", name="Нов", status="approved")     # barsy_article_id NULL
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        res = await intake._save_to_barsy(s, d, SimpleNamespace(email="o@x.bg", id="u"))
    assert res["created"] is True and res["article_id"] == 12345       # mock_barsy returns 12345
    assert [op for op, _ in mock_barsy].count("Articles_create") == 1  # created exactly once


# --- G6: a resolved Shopify leaf sets cat_id/master_cat_id on the Barsy create body ---
async def test_save_to_barsy_sets_category_from_leaf(make_draft, monkeypatch, mock_barsy):
    from types import SimpleNamespace

    import app.routers.intake as intake
    from app.db import async_session_maker
    from app.models import IntakeDraft
    monkeypatch.setattr(intake, "get_settings", lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))
    monkeypatch.setattr(intake, "_resolve_cat_id", lambda sid: 3803 if sid else None)
    did = await make_draft(code="Z", name="С категория", status="approved", shopify_category_id="hg-1-2")
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        await intake._save_to_barsy(s, d, SimpleNamespace(email="o@x.bg", id="u"))
    body = next(b for op, b in mock_barsy if op == "Articles_create")["article"]
    assert body["cat_id"] == 3803 and body["master_cat_id"] == 3803


# --- delivery traceability: supplier code → ref_num, raw supplier name → alias (live path) ---
async def test_save_to_barsy_sets_ref_num_and_alias_from_delivery_line(make_draft, monkeypatch, mock_barsy):
    from types import SimpleNamespace

    import app.routers.intake as intake
    from app.db import async_session_maker
    from app.models import DeliveryLine, IntakeDraft
    monkeypatch.setattr(intake, "get_settings", lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))
    monkeypatch.setattr(intake, "_resolve_cat_id", lambda sid: None)
    async with async_session_maker() as s:
        line = DeliveryLine(batch_id=1, supplier_code="SUP-42", name="КУПА КЕР. СТАРО ИМЕ", qty=1, price=1.0)
        s.add(line)
        await s.commit()
        await s.refresh(line)
        line_id = line.id
    did = await make_draft(code="Z", name="Чиста купа", status="approved", delivery_line_id=line_id)
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        await intake._save_to_barsy(s, d, SimpleNamespace(email="o@x.bg", id="u"))
    body = next(b for op, b in mock_barsy if op == "Articles_create")["article"]
    assert body["ref_num"] == "SUP-42"                       # supplier's catalogue code
    assert body["article_aliases"] == ["КУПА КЕР. СТАРО ИМЕ"]  # raw supplier name → search alias


async def test_save_to_barsy_omits_category_when_unresolved(make_draft, monkeypatch, mock_barsy):
    from types import SimpleNamespace

    import app.routers.intake as intake
    from app.db import async_session_maker
    from app.models import IntakeDraft
    monkeypatch.setattr(intake, "get_settings", lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))
    monkeypatch.setattr(intake, "_resolve_cat_id", lambda sid: None)   # unknown leaf / absent checkpoint
    did = await make_draft(code="Z2", name="Без категория", status="approved", shopify_category_id="zzz")
    async with async_session_maker() as s:
        d = await s.get(IntakeDraft, did)
        await intake._save_to_barsy(s, d, SimpleNamespace(email="o@x.bg", id="u"))
    body = next(b for op, b in mock_barsy if op == "Articles_create")["article"]
    assert "cat_id" not in body and "master_cat_id" not in body       # uncategorised, not junk
