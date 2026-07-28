# -*- coding: utf-8 -*-
"""POST /api/intake/drafts/{id}/push-barsy — the panel's ONE irreversible ERP create. Existing tests
(test_push_guards.py, test_role_gating.py) cover the 404/400/409/403 fences; what was never asserted is
what the endpoint actually SENDS and what it does with what comes back:

  * the Articles_create envelope built from a real draft row (the write itself — mirrors the
    dry-run/live envelope assertions in test_store_ops.py),
  * the Barsy-assigned SKU it reads back off the response,
  * the two paths that must never let a retry double-create an article in the ERP.

barsy_recorder replaces the shared barsy.call, so no real Barsy contact is possible.
"""
import json
from types import SimpleNamespace

import pytest

import app.routers.intake as intake_mod
from app.db import async_session_maker
from app.models import DeliveryLine, IntakeDraft

_RICH = {
    "code": "", "name": "Керамична купа 24см", "unit": "кг", "barcode": "3800130123456",
    "price": "12.50", "brand": "Luminarc", "description": "Голяма керамична купа за салата.",
    "meta_title": "Керамична купа", "meta_description": "Голяма купа за салата",
    "weight_kg": "0.55", "oversell": True, "status": "approved",
    "attributes_json": json.dumps([{"key": "Материал", "value": "керамика"}], ensure_ascii=False),
}


async def _draft_row(draft_id: int) -> IntakeDraft:
    async with async_session_maker() as s:
        return await s.get(IntakeDraft, draft_id)


_LIVE = SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0,
                        barsy_depot_id=1, barsy_currency_id=1, barsy_price_mode=1)


@pytest.fixture(autouse=True)
def _gate_live(monkeypatch):
    """push-barsy is a REAL Articles_create, so it is gated by DELIVERY_BARSY_LIVE (default OFF: one
    pre-launch click would otherwise create a live article). These tests cover the LIVE path, so turn
    the gate on here; the gate-off dry-run has its own tests below."""
    monkeypatch.setattr(intake_mod, "get_settings", lambda: _LIVE)


@pytest.fixture
def created(barsy_recorder):
    """Barsy answers a create with the new article id AND the SKU it generated."""
    barsy_recorder.set_return("Articles_create", {"article_id": 5001, "code": "0000123"})
    return barsy_recorder


# --- what we send to the ERP --------------------------------------------------------
async def test_push_sends_exactly_one_articles_create_with_the_full_envelope(
        owner_client, make_draft, created):
    """The envelope IS the product as it will exist in Barsy (and, downstream, on the storefront).
    A shape slip here — unwrapping {"article": …}, dropping extra_data, sending the barcode as an
    array (Barsy 500s on that), losing the Марка detail — silently corrupts every product staff
    create, and nothing in the panel would show it."""
    draft_id = await make_draft(**_RICH)
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert created.operations == ["Articles_create"]        # one write, no stray reads/updates

    body = created.body_for("Articles_create")
    assert list(body) == ["article"]                        # the wrapper Barsy requires
    art = body["article"]
    assert art["article_name"] == "Керамична купа 24см"
    assert art["article_name_ml"] == {"bg_BG": "Керамична купа 24см"}
    assert art["amount_type_id"] == 3                       # кг → 3 (verified Barsy contract)
    assert art["is_public"] == 1 and art["is_for_sale"] == 1
    assert art["barcode"] == "3800130123456" and isinstance(art["barcode"], str)
    assert art["actual_price"] == "12.50"                   # staff-entered, byte-for-byte (price policy)
    assert art["sale_below_zero"] == "YES"                  # oversell draft → backorderable
    assert art["description_ml"] == {"bg_BG": "Голяма керамична купа за салата."}
    assert art["details"][0] == {"detail_name": "Марка", "detail_value": "Luminarc"}
    assert art["details"][1] == {"detail_name": "Материал", "detail_value": "керамика"}
    assert art["extra_data"] == {"seo_title": "Керамична купа",
                                 "seo_description": "Голяма купа за салата", "weight_kg": "0.55"}


async def test_push_omits_code_so_barsy_assigns_the_sku(owner_client, make_draft, created):
    """Owner's directive: Barsy generates the SKU. Sending `code: ""` is not the same as omitting it —
    it would either be rejected or create an article with a blank SKU that nothing can match."""
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert "code" not in created.body_for("Articles_create")["article"]


async def test_push_sends_our_code_when_the_draft_already_has_one(owner_client, make_draft, created):
    # a delivery-matched product keeps OUR code so the delivery line can resolve by code
    draft_id = await make_draft(code="OUR-1", name="Продукт", status="approved")
    await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert created.body_for("Articles_create")["article"]["code"] == "OUR-1"


# --- what we do with the answer -----------------------------------------------------
async def test_push_records_the_sku_barsy_assigned(owner_client, make_draft, created):
    """We deliberately don't ask staff for a code, so the ONLY moment we can learn it is this
    response. Lose it and the product lives in the panel with no SKU — unmatchable against Barsy,
    against a delivery line, and against the Shopify mirror."""
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.json() == {"ok": True, "barsy_article_id": 5001, "status": "pushed"}
    d = await _draft_row(draft_id)
    assert d.status == "pushed" and d.barsy_article_id == 5001
    assert d.code == "0000123"                              # captured off the create response
    assert d.pushed_at is not None


async def test_push_never_overwrites_a_code_we_already_had(owner_client, make_draft, barsy_recorder):
    # Barsy echoes a code on every create; it must not clobber the code we sent it
    barsy_recorder.set_return("Articles_create", {"article_id": 7, "code": "0009999"})
    draft_id = await make_draft(code="OUR-1", name="Продукт", status="approved")
    await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert (await _draft_row(draft_id)).code == "OUR-1"


async def test_unreadable_id_still_marks_pushed_so_a_retry_cannot_double_create(
        owner_client, make_draft, barsy_recorder):
    """The article EXISTS in Barsy the moment the call returns, whatever the response looks like. If a
    future 'cleaner' version raised (or rolled the draft back) when the id can't be parsed, the
    obvious retry would create a SECOND article in the live ERP — the one failure mode this endpoint
    cannot have. Instead: mark pushed, warn the human to check Barsy."""
    barsy_recorder.set_return("Articles_create", {"result": "ok", "code": "0000777"})  # no parsable id
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["barsy_article_id"] is None and b["status"] == "pushed"
    assert "провери артикула в Barsy" in b["warning"]        # the human is told to verify
    d = await _draft_row(draft_id)
    assert d.status == "pushed" and d.code == "0000777"
    # the retry is refused without touching the ERP again
    again = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert again.status_code == 409
    assert barsy_recorder.operations == ["Articles_create"]


async def test_a_failed_barsy_call_releases_the_claim_for_retry(
        owner_client, make_draft, barsy_recorder):
    """The pre-write claim flips the draft to 'pushing'. If a transport error left it there, the draft
    would be bricked — every retry answers 409 'Създаването вече тече' forever and the only fix is a
    manual DB edit. Barsy's low tier throws busy/timeout errors routinely, so this path is normal."""
    def _boom(_body):
        raise RuntimeError("HTTP 503 on Articles_create: сървъра е зает")

    barsy_recorder.set_return("Articles_create", _boom)
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 502
    assert "Barsy" in r.json()["detail"]
    assert (await _draft_row(draft_id)).status == "approved"     # claim released, not stranded

    barsy_recorder.set_return("Articles_create", {"article_id": 4242})
    ok = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert ok.status_code == 200, ok.text                        # the retry actually works
    assert (await _draft_row(draft_id)).barsy_article_id == 4242


async def test_refused_confirm_leaves_the_draft_pushable(owner_client, make_draft, barsy_recorder):
    """Guard ORDER matters: the confirm check must run BEFORE the approved→pushing claim. Move the
    claim first and every un-confirmed click (the default body!) permanently bricks the draft."""
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": False})
    assert r.status_code == 400
    assert (await _draft_row(draft_id)).status == "approved"     # NOT left in 'pushing'
    barsy_recorder.set_return("Articles_create", {"article_id": 1})
    assert (await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy",
                                    json={"confirm": True})).status_code == 200


# --- delivery-sourced products ------------------------------------------------------
async def test_a_delivery_sourced_draft_is_pushable(owner_client, make_draft, created):
    """Products that arrive on a delivery sit in 'done_from_delivery', not 'approved'. Narrowing the
    claim to 'approved' alone would make every delivery product unpushable with a misleading
    'Създаването вече тече' — and deliveries can't finalise until their new products exist."""
    draft_id = await make_draft(code="", name="Нов от доставка", status="done_from_delivery")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert (await _draft_row(draft_id)).status == "pushed"


async def test_unreadable_id_still_reconciles_the_delivery_line_by_code(
        owner_client, make_draft, barsy_recorder):
    """Companion to the unreadable-id case, on the delivery path. The article WAS created, so the line
    must stop being 'new' and carry our code — otherwise the delivery's finalise gate never clears and
    the storeload can't be sent, even though the product exists in Barsy."""
    barsy_recorder.set_return("Articles_create", {"result": "ok", "code": "0000555"})
    async with async_session_maker() as s:
        line = DeliveryLine(batch_id=1, name="Купа от доставчик", qty=3, is_new=True)
        s.add(line)
        await s.commit()
        await s.refresh(line)
        line_id = line.id

    draft_id = await make_draft(code="", name="Купа", status="done_from_delivery",
                                delivery_line_id=line_id)
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    async with async_session_maker() as s:
        ln = await s.get(DeliveryLine, line_id)
    assert ln.is_new is False                 # no longer a "new product" blocking finalise
    assert ln.match_code == "0000555"         # resolvable by code even without an article_id
    assert ln.article_id is None              # honestly unknown — not a guessed/zero id


# --- roles --------------------------------------------------------------------------
async def test_packer_cannot_push(packer_client, make_draft, barsy_recorder):
    # owner-only ERP write; test_role_gating.py covers staff + anonymous, packer is the lowest role
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await packer_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 403
    assert barsy_recorder.operations == []    # refused before any ERP contact


# --- the write gate (bug found by this lane: push-barsy was UNGATED) -----------------
async def test_gate_off_does_not_touch_barsy_and_leaves_the_draft_pushable(
        owner_client, make_draft, barsy_recorder, monkeypatch):
    """DELIVERY_BARSY_LIVE off = a rehearsal. No Articles_create may reach the ERP, and the draft must
    stay pushable (the claim released), or a pre-launch click would strand it in 'pushing' forever."""
    monkeypatch.setattr(intake_mod, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=False, barsy_min_profit_percent=0.0))
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["dry_run"] is True and b["gated"] is True
    assert "Articles_create" not in barsy_recorder.operations   # nothing reached Barsy
    assert (await _draft_row(draft_id)).status == "approved"      # still pushable once the gate is on


async def test_push_applies_the_category_and_profit_floor_like_the_delivery_path(
        owner_client, make_draft, created, monkeypatch):
    """Parity bug: push-barsy called the pure builder only, so a product created from Нов продукт landed
    UNCATEGORISED and without the POS below-cost floor, unlike the identical product saved via a
    delivery (_save_to_barsy resolves cat_id + sets min_profit_percent)."""
    monkeypatch.setattr(intake_mod, "_resolve_cat_id", lambda sid: 77)
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    art = created.body_for("Articles_create")["article"]
    assert art["cat_id"] == 77 and art["master_cat_id"] == 77
    assert "min_profit_percent" in art
