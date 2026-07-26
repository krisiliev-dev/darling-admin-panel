# -*- coding: utf-8 -*-
"""Audit trail: every mutating request is logged (middleware) + semantic events, viewable/filterable."""
import pytest


@pytest.mark.asyncio
async def test_mutations_are_logged_and_viewable(owner_client):
    await owner_client.post("/api/brands", json={"name": "ЛогТест"})
    r = await owner_client.get("/api/logs")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # the request itself (middleware) AND the semantic event are both logged
    assert any(i["action"] == "http:POST" and i["target"] == "/api/brands" for i in items)
    assert any(i["action"] == "brand.add" for i in items)
    # actor is resolved (from the cookie for http rows, from the actor for semantic rows)
    assert any(i["actor"] and i["actor"] != "—" for i in items)


@pytest.mark.asyncio
async def test_logs_filters_and_owner_gate(owner_client, staff_client):
    await owner_client.post("/api/brands", json={"name": "Филтър"})
    by_action = (await owner_client.get("/api/logs", params={"action": "brand.add"})).json()
    assert by_action["items"] and all("brand.add" in i["action"] for i in by_action["items"])
    since = (await owner_client.get("/api/logs", params={"date_from": "2020-01-01"})).json()
    assert since["total"] >= 1
    acts = (await owner_client.get("/api/logs/actions")).json()["actions"]
    assert "brand.add" in acts and "http:POST" in acts
    # the audit trail is owner-only
    assert (await staff_client.get("/api/logs")).status_code == 403
    assert (await staff_client.get("/api/logs/actions")).status_code == 403
