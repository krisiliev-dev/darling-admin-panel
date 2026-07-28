# -*- coding: utf-8 -*-
"""Push-to-Barsy guards. The push endpoint is a real, irreversible ERP write, so it is
fenced by several pre-conditions. These run as the owner (the only allowed role) and
monkeypatch the Barsy client so no real ERP call can happen.

Guard order in the handler is: 404 (missing draft) -> 400 (confirm) -> atomic approved->pushing
claim -> 409 (already pushed / in flight). NB: there is NO 'missing code' guard — Barsy assigns the
SKU on create and returns it.
"""
from types import SimpleNamespace

import pytest

import app.routers.intake as intake_mod


@pytest.fixture(autouse=True)
def _delivery_gate_live(monkeypatch):
    """push-barsy is a REAL Articles_create and is gated by DELIVERY_BARSY_LIVE (default OFF, so a
    pre-launch click can't create a live article). These tests exercise the LIVE path — the gate-off
    dry-run has dedicated tests in test_intake_push_barsy.py."""
    monkeypatch.setattr(intake_mod, "get_settings",
                        lambda: SimpleNamespace(delivery_barsy_live=True, barsy_min_profit_percent=0.0))


async def test_push_without_confirm_is_400(owner_client, make_draft, mock_barsy):
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")
    r = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": False})
    assert r.status_code == 400
    assert len(mock_barsy) == 0  # never reached the ERP


async def test_push_default_confirm_false_is_400(owner_client, make_draft, mock_barsy):
    # confirm defaults to False, so an empty body must also be refused
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")
    r = await owner_client.post(f"/api/intake/drafts/{draft_id}/push-barsy", json={})
    assert r.status_code == 400
    assert len(mock_barsy) == 0


async def test_push_without_code_succeeds_barsy_assigns(owner_client, make_draft, mock_barsy):
    # no code needed — Barsy generates the SKU on create and returns it (owner's directive)
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert len(mock_barsy) == 1              # the ERP create WAS called


async def test_push_already_pushed_is_409(owner_client, make_draft, mock_barsy):
    draft_id = await make_draft(
        code="SKU-1", name="Продукт", status="pushed", barsy_article_id=999)
    r = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 409
    assert len(mock_barsy) == 0  # double-push blocked before any ERP write


async def test_push_missing_draft_is_404(owner_client, mock_barsy):
    r = await owner_client.post(
        "/api/intake/drafts/999999/push-barsy", json={"confirm": True})
    assert r.status_code == 404
    assert len(mock_barsy) == 0


async def test_valid_push_marks_pushed_once(owner_client, make_draft, mock_barsy):
    """Happy path with the ERP mocked: status flips to pushed, id recorded, and a second
    push of the same draft is refused (no duplicate ERP call)."""
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")

    first = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert first.status_code == 200, first.text
    assert first.json()["barsy_article_id"] == 12345
    assert len(mock_barsy) == 1

    # replaying the push is now a 409 and does not call the ERP again
    second = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert second.status_code == 409
    assert len(mock_barsy) == 1
