# -*- coding: utf-8 -*-
"""Bulk acknowledge of Проблеми (owner 2026-08-03: "wipe the problems").

An issue row is the only place a sync failure is visible, so clearing them in bulk is exactly the
operation that must be hard to do by accident: owner-only, a written reason, and nothing deleted.
These assert that contract against the real endpoint, not the helper.
"""
import pytest


async def _seed_issues():
    """Two open issues + one already resolved, written straight into the table like the webhook
    receiver does."""
    from app.db import async_session_maker
    from app.models import ProcessingIssue
    async with async_session_maker() as s:
        for ref, resolved in (("#a", False), ("#b", False), ("#c", True)):
            s.add(ProcessingIssue(source="shopify", topic="orders/create", order_ref=ref,
                                  severity="error", kind="exception", summary=f"issue {ref}",
                                  detail="{}", count=1, resolved=resolved))
        await s.commit()


@pytest.mark.asyncio
async def test_resolve_all_clears_every_open_issue_and_keeps_the_rows(owner_client):
    await _seed_issues()
    assert (await owner_client.get("/api/issues")).json()["open_count"] == 2

    r = await owner_client.post("/api/issues/resolve-all",
                                json={"reason": "тестови поръчки от Shopify, вече не важат"})
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] == 2

    after = (await owner_client.get("/api/issues")).json()
    assert after["open_count"] == 0
    # nothing is DELETED — the rows stay, marked, so the history survives the clear
    assert len(after["issues"]) == 3
    assert all(i["resolved"] for i in after["issues"])
    cleared = [i for i in after["issues"] if i["order_ref"] in ("#a", "#b")]
    assert all(i["resolved_by"] for i in cleared), "who cleared it must be recorded"


@pytest.mark.asyncio
async def test_the_reason_lands_in_the_audit_log(owner_client):
    """The reason is the whole point: it carries what we learned out of the row before the row
    stops being visible."""
    await _seed_issues()
    await owner_client.post("/api/issues/resolve-all", json={"reason": "остатъци от тестовете"})
    items = (await owner_client.get("/api/logs")).json()["items"]
    row = next((i for i in items if i["action"] == "issues.resolve_all"), None)
    assert row is not None, "the bulk clear must be an auditable event"
    assert "остатъци от тестовете" in (row.get("detail") or "")


@pytest.mark.asyncio
async def test_a_reason_is_mandatory(owner_client):
    await _seed_issues()
    for bad in ("", "   ", "ок"):
        r = await owner_client.post("/api/issues/resolve-all", json={"reason": bad})
        assert r.status_code == 400, f"{bad!r} should be refused, got {r.status_code}"
    assert (await owner_client.get("/api/issues")).json()["open_count"] == 2


@pytest.mark.asyncio
async def test_a_non_owner_cannot_bulk_clear(staff_client):
    await _seed_issues()
    r = await staff_client.post("/api/issues/resolve-all", json={"reason": "нека пробвам"})
    assert r.status_code in (401, 403), r.text
    # …and staff can still READ them: the gate is on the bulk write, not on visibility
    assert (await staff_client.get("/api/issues")).json()["open_count"] == 2


@pytest.mark.asyncio
async def test_clearing_an_empty_board_is_a_no_op(owner_client):
    r = await owner_client.post("/api/issues/resolve-all", json={"reason": "нищо за чистене"})
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] == 0
