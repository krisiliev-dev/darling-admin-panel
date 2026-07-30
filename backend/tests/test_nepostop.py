# -*- coding: utf-8 -*-
"""nepostop COD-refuser lookup. These tests exist to pin the three properties the owner and the GDPR
position depend on: it is ADVISORY (never a decision), it FAILS OPEN (a dead service must not flag or
block anyone), and 'we did not check' is never rendered as 'clean'.

The contributing half (report_shipment) is gated separately from lookups, because sending a customer's
phone number outward is a disclosure, not a query — a test asserts the gate actually holds.
"""
import io
import urllib.error
from types import SimpleNamespace

from app import nepostop


def _settings(**over):
    base = {"nepostop_api_key": "k", "nepostop_api_base": "https://nepostop.com/api/v1",
            "nepostop_report_live": False}
    base.update(over)
    return lambda: SimpleNamespace(**base)


def _patch(monkeypatch, payload: bytes | None = None, exc: Exception | None = None, **over):
    monkeypatch.setattr(nepostop, "get_settings", _settings(**over))
    seen: dict = {}

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["body"] = req.data
        if exc:
            raise exc
        return _R(payload or b"{}")
    monkeypatch.setattr(nepostop.urllib.request, "urlopen", _open)
    return seen


_HIT = b'{"phone_number":"0888123456","found":true,"stats":{"total":9,"delivered":2,"undelivered":7,"undelivered_percentage":77.8}}'


def test_a_hit_is_reported_with_source_grade_detail(monkeypatch):
    """Counts, not a score: a flagged customer must be explainable ('7 of 9 refused'), because
    'flagged' is unanswerable to someone who disputes it."""
    seen = _patch(monkeypatch, _HIT)
    r = nepostop.check_phone("0888 123 456")
    assert r["checked"] is True and r["found"] is True
    assert r["total"] == 9 and r["refused"] == 7 and r["refused_pct"] == 77.8
    assert seen["url"].endswith("/phone-check")
    assert seen["headers"]["x-api-key"] == "k"


def test_service_down_is_UNCHECKED_not_clean(monkeypatch):
    """THE fail-open test. A dead registry must not silently become 'this customer is fine' — and it
    must not flag them either. `checked=False` is the honest third state."""
    _patch(monkeypatch, exc=urllib.error.URLError("connection refused"))
    r = nepostop.check_phone("0888123456")
    assert r["checked"] is False and r["found"] is False
    assert r["total"] is None                     # NOT 0 — we did not look, we did not find zero
    assert r["error"]


def test_rate_limit_is_named_so_staff_dont_blame_the_customer(monkeypatch):
    _patch(monkeypatch, exc=urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None))
    assert nepostop.check_phone("0888123456")["error"] == "rate_limited"


def test_no_key_means_disabled_and_no_network(monkeypatch):
    seen = _patch(monkeypatch, _HIT, nepostop_api_key="")
    r = nepostop.check_phone("0888123456")
    assert r["enabled"] is False and r["checked"] is False
    assert "url" not in seen                       # never called


def test_garbled_response_is_unchecked(monkeypatch):
    _patch(monkeypatch, b"<html>502</html>")
    assert nepostop.check_phone("0888123456")["checked"] is False


def test_missing_stats_does_not_invent_numbers(monkeypatch):
    _patch(monkeypatch, b'{"found": false}')
    r = nepostop.check_phone("0888123456")
    assert r["checked"] is True and r["found"] is False
    assert r["total"] is None and r["refused"] is None


# ---------------------------------------------------------------- contributing (separate gate) ---
def test_reporting_is_OFF_by_default_and_sends_nothing(monkeypatch):
    """Sending a customer's phone outward is a disclosure, not a query, so it has its own gate. With
    the gate off we return the body we WOULD have sent — exercisable without disclosing anything."""
    seen = _patch(monkeypatch, b'{"ok":true}')
    r = nepostop.report_shipment("0888123456", "ECONT-77")
    assert r["dry_run"] is True and r["sent"] is False
    assert r["body"] == {"phone_number": "0888123456", "shipping_number": "ECONT-77"}
    assert "url" not in seen                       # nothing left the building


def test_reporting_sends_when_explicitly_enabled(monkeypatch):
    seen = _patch(monkeypatch, b'{"ok":true}', nepostop_report_live=True)
    r = nepostop.report_shipment("0888123456", "ECONT-77")
    assert r["sent"] is True and seen["url"].endswith("/shipments")


def test_a_failed_contribution_never_affects_the_order(monkeypatch):
    """Our upload to a registry failing is our problem. It must not surface as an order error."""
    _patch(monkeypatch, exc=TimeoutError("slow"), nepostop_report_live=True)
    r = nepostop.report_shipment("0888123456", "ECONT-77")
    assert r["ok"] is False and r["sent"] is False and r["error"]


def test_reporting_refuses_incomplete_input(monkeypatch):
    _patch(monkeypatch, b"{}", nepostop_report_live=True)
    assert nepostop.report_shipment("", "ECONT-77")["ok"] is False
    assert nepostop.report_shipment("0888123456", "")["ok"] is False
