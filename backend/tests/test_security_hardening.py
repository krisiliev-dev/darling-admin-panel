# -*- coding: utf-8 -*-
"""Regression tests for the security-audit fixes.
  * H-1: self-service PATCH /me cannot escalate role (role dropped from UserUpdate).
  * H-2: the rate-limit client key ignores spoofable X-Forwarded-For unless a proxy is trusted.
  * input bounds: negative COD / oversized batches are rejected at the schema layer.
"""
import types

import pytest

from app import ratelimit
from app.routers.orders import ManualShip
from app.routers.cod import SettleRequest, SettleLine


# --- H-1: privilege escalation via mass-assignment ----------------------------------
async def test_packer_cannot_self_escalate_role(packer_client):
    # a packer tries to promote itself to manager via the self-service /me endpoint
    r = await packer_client.patch("/api/users/me", json={"role": "manager"})
    # the field is silently ignored (pydantic drops unknown keys), not applied
    assert r.status_code in (200, 400)
    me = await packer_client.get("/api/users/me")
    assert me.status_code == 200
    assert me.json()["role"] == "packer"  # role unchanged
    assert me.json()["is_superuser"] is False


async def test_self_service_can_still_edit_full_name(packer_client):
    r = await packer_client.patch("/api/users/me", json={"full_name": "Нов Пакер"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Нов Пакер"
    assert r.json()["role"] == "packer"  # editing profile doesn't touch privilege


# --- H-2: X-Forwarded-For must not be trusted by default ----------------------------
def _fake_request(xff=None, host="203.0.113.9"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return types.SimpleNamespace(
        headers=types.SimpleNamespace(get=lambda k, d=None: headers.get(k, d)),
        client=types.SimpleNamespace(host=host),
    )


def test_client_ip_ignores_xff_by_default(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings",
                        lambda: types.SimpleNamespace(trust_proxy=False))
    # attacker spoofs a random XFF — we must fall back to the real peer address
    assert ratelimit.client_ip(_fake_request(xff="1.1.1.1", host="203.0.113.9")) == "203.0.113.9"
    assert ratelimit.client_ip(_fake_request(xff="9.9.9.9", host="203.0.113.9")) == "203.0.113.9"


def test_client_ip_uses_last_hop_when_proxy_trusted(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings",
                        lambda: types.SimpleNamespace(trust_proxy=True))
    # trusted proxy appends the real client last; attacker-supplied leftmost entries are ignored
    assert ratelimit.client_ip(_fake_request(xff="1.1.1.1, 203.0.113.9")) == "203.0.113.9"


# --- input bounds -------------------------------------------------------------------
def test_manualship_rejects_negative_cod():
    with pytest.raises(Exception):
        ManualShip(courier="speedy", recipient="Иван", phone="0888", cod_amount=-5)


def test_settle_rejects_oversized_batch():
    lines = [SettleLine(order_id=str(i), collected=1.0) for i in range(1001)]
    with pytest.raises(Exception):
        SettleRequest(lines=lines)
