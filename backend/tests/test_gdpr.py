# -*- coding: utf-8 -*-
"""ОРЗД/GDPR data-subject tool: look up + erase the panel's own data for a phone, retracting any
nekorekten report we published. Owner-only."""
import pytest

from app import nekorekten


async def _record(client, phone, outcome="refused", order="#1"):
    return await client.post("/api/risk/outcome", json={"phone": phone, "outcome": outcome, "order_name": order})


@pytest.fixture(autouse=True)
def _nk(monkeypatch):
    monkeypatch.setattr(nekorekten, "check_phone", lambda p: {"enabled": True, "reports": 0})
    monkeypatch.setattr(nekorekten, "enabled", lambda: True)


async def test_lookup_shows_held_data(owner_client):
    await _record(owner_client, "0888123456")
    r = await owner_client.get("/api/gdpr/lookup?phone=%2B359888123456")
    assert r.status_code == 200
    b = r.json()
    assert b["phone"] == "359888123456" and b["count"] == 1
    assert b["outcomes"][0]["outcome"] == "refused"


async def test_erase_deletes_and_retracts(owner_client, monkeypatch):
    deleted = []
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: deleted.append(rid) or {"ok": True})
    monkeypatch.setattr(nekorekten, "report", lambda *a, **k: {"ok": True, "report_id": "R1"})
    await _record(owner_client, "0888123456", order="#7")
    await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "Отказана поръчка тук.", "confirm": True})

    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888123456", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["erased"] >= 1 and body["reports_retracted"] == 1 and deleted == ["R1"]
    # data is gone
    assert (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()["count"] == 0


async def test_erase_keeps_row_when_retraction_fails(owner_client, monkeypatch):
    """If the nekorekten retraction fails, the local row (which holds the report id) must NOT be
    deleted — otherwise the published PII is orphaned with no way to retract it. The failure is
    surfaced so the owner can retry."""
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: {"ok": False, "error": "5xx"})
    monkeypatch.setattr(nekorekten, "report", lambda *a, **k: {"ok": True, "report_id": "R9"})
    await _record(owner_client, "0888123456", order="#9")
    await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "Отказана поръчка тук.", "confirm": True})

    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888123456", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["reports_retracted"] == 0
    assert body.get("retraction_failed")               # failure surfaced
    # the reported row is retained (still holds R9 for a retry), so lookup still finds it
    assert (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()["count"] == 1


async def test_erase_requires_confirm(owner_client):
    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888", "confirm": False})
    assert r.status_code == 400


async def test_gdpr_is_owner_only(staff_client):
    assert (await staff_client.get("/api/gdpr/lookup?phone=0888")).status_code in (401, 403)
    assert (await staff_client.post("/api/gdpr/erase", json={"phone": "0888", "confirm": True})).status_code in (401, 403)
