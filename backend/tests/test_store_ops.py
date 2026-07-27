# -*- coding: utf-8 -*-
"""Склад store-ops (/api/store-ops): reads (depots, write-off reasons, movement ledger) + the gated
writes (manual Зареждане, Изписване, Прехвърляне, Сторно). The gate defaults OFF, so writes DRY-RUN:
they return the exact Barsy body and send NOTHING. With the gate on, the body reaches barsy.call."""
from types import SimpleNamespace

from app.routers import store_ops


def _live_settings(**over):
    base = {"store_ops_barsy_live": True, "barsy_depot_id": 1,
            "barsy_currency_id": 1, "barsy_price_mode": 1}
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- reads ---
async def test_depots_lists(owner_client, barsy_recorder):
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": 1, "depot_name": "Основен"}])
    r = await owner_client.get("/api/store-ops/depots")
    assert r.status_code == 200, r.text
    assert r.json()["depots"][0]["depot_id"] == 1


async def test_writeoff_reasons_uses_getreasons(owner_client, barsy_recorder):
    barsy_recorder.set_return("Storeouts_getreasons", [{"reason_id": 1, "name": "Липса"}])
    r = await owner_client.get("/api/store-ops/writeoff-reasons")
    assert r.status_code == 200, r.text
    assert r.json()["reasons"][0]["reason_id"] == 1
    assert r.json()["type_id"] == -1
    assert "Storeouts_getreasons" in barsy_recorder.operations   # not the 500-ing *_reasons_getlist


async def test_movements_requires_a_selector(owner_client):
    r = await owner_client.get("/api/store-ops/movements")
    assert r.status_code == 400


async def test_movements_merges_sources_and_survives_a_dead_one(owner_client, barsy_recorder):
    barsy_recorder.set_return("Storeloads_getlist", [{"doc_num": "ZAR-1"}])
    barsy_recorder.set_return("Storemoves_getlist", [{"doc_num": "MOVE-1"}])
    # Storeouts_movements_getlist left unset → recorder default None → treated as empty, no crash
    r = await owner_client.get("/api/store-ops/movements", params={"article_id": 42})
    assert r.status_code == 200, r.text
    kinds = {m["kind"] for m in r.json()["movements"]}
    assert {"load", "transfer"} <= kinds


# ---------------------------------------------------------------- writes: dry-run (gate off) ---
async def test_load_dry_run_writes_nothing(owner_client, barsy_recorder):
    r = await owner_client.post("/api/store-ops/load", json={
        "lines": [{"article_id": 5, "amount": 10, "current_price": 3.5}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["method"] == "Storeloads_create"
    sl = body["barsy_body"]["store_load"]
    assert sl["operation_type"] == 1 and sl["currency_id"] == 1 and sl["price_mode"] == 1
    assert "Storeloads_create" not in barsy_recorder.operations   # nothing sent


async def test_writeoff_dry_run_matches_captured_envelope(owner_client, barsy_recorder):
    r = await owner_client.post("/api/store-ops/writeoff", json={
        "lines": [{"article_id": 7, "amount": 2, "note": "счупено"}], "reason_id": 1,
        "description": "витрина"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["method"] == "Storeouts_save"
    # envelope confirmed from the Barsy UI capture: flat `values` (strings) + sibling `rows`
    vals = body["barsy_body"]["values"]
    assert vals["type_id"] == "-1" and vals["depot_id"] == "1"     # depot flat, string
    row = body["barsy_body"]["rows"][0]
    assert row["article_id"] == "7" and row["amount"] == "2" and row["reason_id"] == "1"
    assert "storeout" not in body["barsy_body"]                    # NOT the old nested shape


async def test_transfer_blocked_when_single_depot(owner_client, barsy_recorder):
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": 1, "depot_name": "Основен"}])
    r = await owner_client.post("/api/store-ops/transfer", json={
        "lines": [{"article_id": 5, "amount": 1}], "depot_from": 1, "depot_to": 2})
    assert r.status_code == 409
    assert "2 склада" in r.json()["detail"]


async def test_transfer_same_depot_rejected(owner_client):
    r = await owner_client.post("/api/store-ops/transfer", json={
        "lines": [{"article_id": 5, "amount": 1}], "depot_from": 1, "depot_to": 1})
    assert r.status_code == 400


async def test_transfer_dry_run_with_two_depots(owner_client, barsy_recorder):
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": 1}, {"depot_id": 2}])
    r = await owner_client.post("/api/store-ops/transfer", json={
        "lines": [{"article_id": 5, "amount": 3}], "depot_from": 1, "depot_to": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["method"] == "Storemoves_create"
    sm = body["barsy_body"]["storemove"]
    assert sm["depot_id_left"] == 1 and sm["depot_id_right"] == 2


# ---------------------------------------------------------------- writes: live (gate on) ---
async def test_load_live_sends_to_barsy(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Storeloads_create", 999)
    r = await owner_client.post("/api/store-ops/load", json={
        "lines": [{"article_id": 5, "amount": 10, "current_price": 3.5}]})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is False and r.json()["result"] == 999
    assert "Storeloads_create" in barsy_recorder.operations


async def test_storno_mirrors_original_as_return(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Storeloads_get", {"depot_id": 1,
                              "articles": [{"article_id": 5, "amount": 10, "current_price": 3.5}]})
    barsy_recorder.set_return("Storeloads_create", 1001)
    r = await owner_client.post("/api/store-ops/storno", json={"storeload_id": 500, "reason": "грешка"})
    assert r.status_code == 200, r.text
    sent = barsy_recorder.body_for("Storeloads_create")["store_load"]
    assert sent["operation_type"] == 2                    # mirror = return
    assert barsy_recorder.body_for("Storeloads_create")["articles"][0]["article_id"] == 5


# ---------------------------------------------------------------- auth ---
async def test_writes_require_owner_or_manager(staff_client):
    r = await staff_client.post("/api/store-ops/load", json={
        "lines": [{"article_id": 5, "amount": 1}]})
    assert r.status_code in (401, 403)


async def test_reads_require_auth(client):
    assert (await client.get("/api/store-ops/depots")).status_code == 401
