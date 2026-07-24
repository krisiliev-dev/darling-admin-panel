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
import pytest

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
