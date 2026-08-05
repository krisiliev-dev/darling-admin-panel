# -*- coding: utf-8 -*-
"""Сметки — read-only accounts browser over Barsy (Accounts_getlist / Accounts_get). Owner-only."""
import pytest


@pytest.mark.asyncio
async def test_list_accounts(owner_client, barsy_recorder):
    barsy_recorder.set_return("Accounts_getlist", [
        {"account_id": 1, "account_sid": "01cfc3a0e", "user_name": "claude", "pos_name": "Каса"},
        {"account_id": 2, "account_sid": "02aa", "user_name": "claude", "pos_name": "Каса"}])
    r = (await owner_client.get("/api/accounts")).json()
    assert len(r["accounts"]) == 2 and r["accounts"][0]["account_id"] == 1
    assert barsy_recorder.body_for("Accounts_getlist")["length"] == 100   # default page


@pytest.mark.asyncio
async def test_list_accounts_respects_limit(owner_client, barsy_recorder):
    barsy_recorder.set_return("Accounts_getlist", [])
    await owner_client.get("/api/accounts?limit=25")
    assert barsy_recorder.body_for("Accounts_getlist")["length"] == 25


@pytest.mark.asyncio
async def test_get_account(owner_client, barsy_recorder):
    barsy_recorder.set_return("Accounts_get", {"account_id": 28, "account_sid": "x", "user_name": "claude"})
    r = (await owner_client.get("/api/accounts/28")).json()
    assert r["account"]["account_id"] == 28


@pytest.mark.asyncio
async def test_get_missing_account_is_404(owner_client, barsy_recorder):
    barsy_recorder.set_return("Accounts_get", {})   # no account_id -> not found
    r = await owner_client.get("/api/accounts/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_accounts_requires_owner(staff_client, barsy_recorder):
    r = await staff_client.get("/api/accounts")
    assert r.status_code in (401, 403)
    assert barsy_recorder.calls == []   # never reached Barsy


@pytest.mark.asyncio
async def test_accounts_requires_auth(client):
    r = await client.get("/api/accounts")
    assert r.status_code == 401
