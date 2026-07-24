# -*- coding: utf-8 -*-
"""COD-refuser risk: phone normalization, internal refusal history, nekorekten lookup (mocked),
and the combined risk level. No real nekorekten calls."""
import pytest

from app import nekorekten
from app.routers import risk as risk_router


def test_phone_normalization():
    assert nekorekten.normalize_phone("0888 123 456") == "359888123456"
    assert nekorekten.normalize_phone("+359 888 123 456") == "359888123456"  # keeps 359 + digits
    assert nekorekten.normalize_phone("888123456") == "359888123456"
    assert nekorekten.normalize_phone("00359888123456") == "359888123456"
    assert nekorekten.normalize_phone("") == ""


def test_report_count_parses_api_shape():
    # the real nekorekten shape: {items:[...], count:N}
    assert nekorekten._report_count({"items": [1, 2, 3], "count": 7, "itemsPerPage": 20}) == 7
    assert nekorekten._report_count({"items": []}) == 0
    assert nekorekten._report_count({"count": 0}) == 0
    assert nekorekten._report_count([1, 2]) == 2          # bare list fallback
    assert nekorekten._report_count({"data": [1]}) == 1   # legacy shape
    assert nekorekten._report_count("weird") == 0


def test_risk_level_thresholds():
    assert risk_router._level(0, 0) == "low"
    assert risk_router._level(1, 0) == "medium"
    assert risk_router._level(0, 1) == "medium"
    assert risk_router._level(2, 0) == "high"
    assert risk_router._level(0, 3) == "high"
    assert risk_router._level(0, None) == "low"


@pytest.fixture
def mock_nk(monkeypatch):
    def _apply(reports):
        monkeypatch.setattr(nekorekten, "check_phone",
                            lambda p: {"enabled": True, "reports": reports})
    return _apply


async def test_lookup_clean_customer(owner_client, mock_nk):
    mock_nk(0)
    r = await owner_client.get("/api/risk/lookup?phone=0888123456")
    assert r.status_code == 200
    b = r.json()
    assert b["phone"] == "359888123456" and b["risk_level"] == "low"
    assert b["internal_refusals"] == 0 and b["nekorekten"]["reports"] == 0
    assert "nekorekten.com" in b["lookup_url"]


async def test_recorded_refusals_raise_risk(owner_client, mock_nk):
    mock_nk(0)  # clean externally, but two internal refusals → high
    for i in range(2):
        r = await owner_client.post("/api/risk/outcome", json={
            "phone": "0888123456", "outcome": "refused", "order_name": f"#{i}", "cod_amount": 20})
        assert r.status_code == 201
    r = await owner_client.get("/api/risk/lookup?phone=+359888123456")
    b = r.json()
    assert b["internal_refusals"] == 2 and b["risk_level"] == "high"


async def test_external_reports_flag_medium(owner_client, mock_nk):
    mock_nk(1)  # one nekorekten report, no internal history → medium
    r = await owner_client.get("/api/risk/lookup?phone=0888999000")
    assert r.json()["risk_level"] == "medium"


async def test_outcome_requires_staff(client, mock_nk):
    r = await client.post("/api/risk/outcome", json={"phone": "0888", "outcome": "refused"})
    assert r.status_code in (401, 403)


async def test_outcome_rejects_bad_value(owner_client, mock_nk):
    r = await owner_client.post("/api/risk/outcome", json={"phone": "0888", "outcome": "maybe"})
    assert r.status_code == 422


@pytest.fixture
def mock_report(monkeypatch):
    calls = []

    def _report(text, phone="", first_name="", last_name="", city_id=None):
        calls.append({"text": text, "phone": phone})
        return {"ok": True, "report_id": "RID123"}
    monkeypatch.setattr(nekorekten, "report", _report)
    monkeypatch.setattr(nekorekten, "enabled", lambda: True)
    return calls


async def test_report_publishes_and_stores_id(owner_client, mock_nk, mock_report):
    mock_nk(0)
    # first record the refusal, then report it
    await owner_client.post("/api/risk/outcome", json={"phone": "0888123456", "outcome": "refused", "order_id": "o9"})
    r = await owner_client.post("/api/risk/report", json={
        "phone": "0888123456", "text": "Отказана поръчка с наложен платеж, разходи за нас.",
        "order_id": "o9", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["report_id"] == "RID123"
    assert mock_report and mock_report[0]["text"].startswith("Отказана")


async def test_report_requires_confirm(owner_client, mock_report):
    r = await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "нещо факт", "confirm": False})
    assert r.status_code == 400


async def test_report_is_owner_only(staff_client, mock_report):
    r = await staff_client.post("/api/risk/report", json={"phone": "0888", "text": "някакъв текст", "confirm": True})
    assert r.status_code in (401, 403)


async def test_retract_deletes_and_clears_id(owner_client, mock_nk, mock_report, monkeypatch):
    mock_nk(0)
    deleted = []
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: deleted.append(rid) or {"ok": True})
    # file a report (mock_report returns RID123 + stores it on the outcome)
    await owner_client.post("/api/risk/outcome", json={"phone": "0888123456", "outcome": "refused", "order_id": "o1"})
    await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "Отказана поръчка тук.", "order_id": "o1", "confirm": True})
    # retract it
    r = await owner_client.post("/api/risk/report/retract", json={"report_id": "RID123", "confirm": True})
    assert r.status_code == 200 and deleted == ["RID123"]


async def test_retract_requires_owner(staff_client):
    r = await staff_client.post("/api/risk/report/retract", json={"report_id": "X", "confirm": True})
    assert r.status_code in (401, 403)


async def test_report_without_prior_outcome_is_retractable(owner_client, mock_nk, mock_report, monkeypatch):
    # H1: reporting straight from the shipment tool (no prior /outcome) must still be retractable
    mock_nk(0)
    deleted = []
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: deleted.append(rid) or {"ok": True})
    r = await owner_client.post("/api/risk/report", json={"phone": "0899000111", "text": "Отказана поръчка тук.", "confirm": True})
    assert r.status_code == 200 and r.json()["report_id"] == "RID123"
    # listed as outstanding, retractable even in a fresh session
    lst = (await owner_client.get("/api/risk/reports")).json()["reports"]
    assert any(x["report_id"] == "RID123" for x in lst)
    assert (await owner_client.post("/api/risk/report/retract", json={"report_id": "RID123", "confirm": True})).status_code == 200
    assert deleted == ["RID123"]


async def test_retract_rejects_unknown_id(owner_client):
    # M4: never delete an id we didn't file
    r = await owner_client.post("/api/risk/report/retract", json={"report_id": "NOTOURS", "confirm": True})
    assert r.status_code == 404
