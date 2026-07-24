# -*- coding: utf-8 -*-
"""Push-to-Barsy guards. The push endpoint is a real, irreversible ERP write, so it is
fenced by several pre-conditions. These run as the owner (the only allowed role) and
monkeypatch the Barsy client so no real ERP call can happen.

Guard order in the handler is: 404 (missing draft) -> 400 (missing code) -> 400 (confirm)
-> atomic approved->pushing claim -> 409 (already pushed / in flight).
"""


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


async def test_push_without_code_is_400(owner_client, make_draft, mock_barsy):
    # missing business key (code) — rejected even with confirm=true
    draft_id = await make_draft(code="", name="Продукт", status="approved")
    r = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 400
    assert len(mock_barsy) == 0


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
