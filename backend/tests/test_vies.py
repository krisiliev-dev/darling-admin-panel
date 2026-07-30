# -*- coding: utf-8 -*-
"""VIES ДДС validation. The whole point of these tests is the THREE-state contract: an unreachable
registry must never be reported as an invalid VAT number. Conflating "the service was down" with "this
number is fake" would stop a legitimate invoice — the expensive direction of a fail-open/fail-closed
mix-up, and the one this module exists to avoid."""
import io
import urllib.error

from app import vies


def _resp(payload: bytes):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _R(payload)


def _patch(monkeypatch, payload: bytes | None = None, exc: Exception | None = None):
    def _open(req, timeout=None):
        if exc:
            raise exc
        return _resp(payload or b"{}")
    monkeypatch.setattr(vies.urllib.request, "urlopen", _open)


# ---------------------------------------------------------------- parsing ---
def test_split_vat_handles_prefix_spacing_and_bare_numbers():
    assert vies.split_vat("BG 831391124") == ("BG", "831391124")
    assert vies.split_vat("bg-831.391.124") == ("BG", "831391124")
    assert vies.split_vat("831391124") == ("BG", "831391124")     # bare → Bulgarian, the usual case
    assert vies.split_vat("DE123456789") == ("DE", "123456789")
    assert vies.split_vat("") == ("", "")


# ---------------------------------------------------------------- the three states ---
def test_valid_number_is_verified_and_returns_the_company(monkeypatch):
    _patch(monkeypatch, b'{"isValid": true, "userError": "VALID", "name": "\\u0424\\u0418\\u0420\\u041c\\u0410", "address": " \\u0421\\u043e\\u0444\\u0438\\u044f  1000 "}')
    r = vies.check_vat("BG831391124")
    assert r["status"] == vies.VERIFIED
    assert r["name"] == "ФИРМА"
    assert r["address"] == "София 1000"           # whitespace collapsed


def test_authoritative_invalid_is_not_verified(monkeypatch):
    _patch(monkeypatch, b'{"isValid": false, "userError": "INVALID"}')
    assert vies.check_vat("BG000000000")["status"] == vies.NOT_VERIFIED


def test_member_state_unavailable_is_UNCHECKED_not_invalid(monkeypatch):
    """THE test. VIES answering 'the Bulgarian registry is down' must not read as 'the number is bad'."""
    _patch(monkeypatch, b'{"isValid": false, "userError": "MS_UNAVAILABLE"}')
    r = vies.check_vat("BG831391124")
    assert r["status"] == vies.UNCHECKED
    assert r["status"] != vies.NOT_VERIFIED
    assert "регистър" in r["reason"]


def test_service_busy_is_unchecked(monkeypatch):
    _patch(monkeypatch, b'{"isValid": false, "userError": "GLOBAL_MAX_CONCURRENT_REQ"}')
    assert vies.check_vat("BG831391124")["status"] == vies.UNCHECKED


def test_network_failure_is_unchecked_and_never_raises(monkeypatch):
    _patch(monkeypatch, exc=urllib.error.URLError("no route to host"))
    r = vies.check_vat("BG831391124")
    assert r["status"] == vies.UNCHECKED and "VIES" in r["reason"]


def test_timeout_is_unchecked(monkeypatch):
    _patch(monkeypatch, exc=TimeoutError("timed out"))
    assert vies.check_vat("BG831391124")["status"] == vies.UNCHECKED


def test_garbled_response_is_unchecked(monkeypatch):
    _patch(monkeypatch, b"<html>gateway error</html>")
    assert vies.check_vat("BG831391124")["status"] == vies.UNCHECKED


def test_malformed_input_never_reaches_the_network(monkeypatch):
    called = {"n": 0}

    def _open(req, timeout=None):
        called["n"] += 1
        return _resp(b"{}")
    monkeypatch.setattr(vies.urllib.request, "urlopen", _open)
    for bad in ("", "   ", "BG", "BG!!!", "x" * 40):
        assert vies.check_vat(bad)["status"] == vies.UNCHECKED
    assert called["n"] == 0                        # no pointless calls to a slow public service


# ---------------------------------------------------------------- status probe ---
def test_member_state_availability_is_best_effort(monkeypatch):
    _patch(monkeypatch, b'{"countries": [{"countryCode": "BG", "availability": "Available"}]}')
    assert vies.member_state_available("bg") is True
    _patch(monkeypatch, b'{"countries": [{"countryCode": "BG", "availability": "Unavailable"}]}')
    assert vies.member_state_available("BG") is False
    _patch(monkeypatch, exc=urllib.error.URLError("down"))
    assert vies.member_state_available("BG") is None   # unknown, not False — never invent a verdict
