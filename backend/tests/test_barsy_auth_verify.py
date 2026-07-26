# -*- coding: utf-8 -*-
"""Transport-level tests for barsy_auth.verify (previously untested — it was always mocked).
We stub httpx.post so no real ERP call happens, and assert the credential/availability semantics:
2xx+user → profile; 4xx → None (bad password); 5xx → RuntimeError (Barsy down); connection error →
RuntimeError."""
import httpx
import pytest

from app import barsy_auth


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setattr(barsy_auth, "_barsy_base", lambda: "https://x.barsy.online/endpoints/json")


def test_verify_returns_profile_on_valid_login(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"user_id": 5, "role_id": 3}))
    assert barsy_auth.verify("u", "p") == {"user_id": 5, "role_id": 3}


def test_verify_returns_none_on_bad_credentials(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401))
    assert barsy_auth.verify("u", "bad") is None


def test_verify_returns_none_on_error_body(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"error": "nope"}))
    assert barsy_auth.verify("u", "p") is None


def test_verify_raises_on_barsy_down(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(503))
    with pytest.raises(RuntimeError):
        barsy_auth.verify("u", "p")


def test_verify_raises_on_transport_error(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(RuntimeError):
        barsy_auth.verify("u", "p")


def test_verify_empty_creds_short_circuit():
    assert barsy_auth.verify("", "") is None
