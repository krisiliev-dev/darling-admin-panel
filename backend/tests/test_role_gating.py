# -*- coding: utf-8 -*-
"""Role gating — owner-only endpoints must refuse a logged-in non-owner (staff) with 403,
and allow the owner. Covers the two owner-only surfaces:
  * POST /api/auth/staff            (only the owner creates accounts)
  * POST /api/intake/drafts/{id}/push-barsy  (real ERP write — owner only)
"""


# --- POST /api/auth/staff -----------------------------------------------------------
async def test_staff_cannot_create_accounts(staff_client):
    r = await staff_client.post("/api/auth/staff", json={
        "email": "another@example.com", "password": "pw-123456", "role": "staff"})
    assert r.status_code == 403


async def test_owner_can_create_accounts(owner_client):
    r = await owner_client.post("/api/auth/staff", json={
        "email": "packer@example.com", "password": "pw-123456", "role": "packer"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "packer"
    assert r.json()["is_superuser"] is False  # staff/packer never superuser


# --- POST /api/intake/drafts/{id}/push-barsy ----------------------------------------
async def test_staff_cannot_push_to_barsy(staff_client, make_draft):
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")
    r = await staff_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 403  # owner-only; refused before any ERP logic runs


async def test_owner_is_allowed_to_push(owner_client, make_draft, mock_barsy):
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")
    r = await owner_client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "pushed"
    assert body["barsy_article_id"] == 12345
    # exactly one (mocked) ERP call was made — no real Barsy contact
    assert len(mock_barsy) == 1
    operation, sent = mock_barsy[0]
    assert operation == "Articles_create"
    assert sent["article"]["code"] == "SKU-1"


async def test_unauthenticated_push_is_rejected(client, make_draft):
    draft_id = await make_draft(code="SKU-1", name="Продукт", status="approved")
    r = await client.post(
        f"/api/intake/drafts/{draft_id}/push-barsy", json={"confirm": True})
    assert r.status_code == 401
