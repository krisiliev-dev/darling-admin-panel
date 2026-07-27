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
    assert body["barsy_body"]["action_type"] == "save_and_close"   # posts → decrements stock
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
async def test_load_live_needs_confirm_then_sends(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Storeloads_create", 999)
    # gate ON without confirm → 400 (a stray call can't mutate stock)
    r0 = await owner_client.post("/api/store-ops/load", json={"lines": [{"article_id": 5, "amount": 10}]})
    assert r0.status_code == 400 and "Storeloads_create" not in barsy_recorder.operations
    # with confirm → sends
    r = await owner_client.post("/api/store-ops/load", json={
        "lines": [{"article_id": 5, "amount": 10, "current_price": 3.5}], "confirm": True})
    assert r.status_code == 200 and r.json()["dry_run"] is False and r.json()["result"] == 999
    assert "Storeloads_create" in barsy_recorder.operations


async def test_storno_dry_run_mirrors_lines_as_return(owner_client, barsy_recorder):
    # storno now takes explicit lines (Barsy has no Storeloads_get) → op_type 2 return
    r = await owner_client.post("/api/store-ops/storno", json={
        "lines": [{"article_id": 5, "amount": 10, "current_price": 3.5}],
        "storeload_id": 500, "reason": "грешка"})
    assert r.status_code == 200, r.text
    body = r.json()["barsy_body"]
    assert body["store_load"]["operation_type"] == 2 and body["articles"][0]["article_id"] == "5"
    assert "Storeloads_get" not in barsy_recorder.operations   # the nonexistent read is gone


async def test_storno_requires_lines(owner_client):
    r = await owner_client.post("/api/store-ops/storno", json={"storeload_id": 500})
    assert r.status_code == 422   # lines is required (min_length 1)


# ---------------------------------------------------------------- auth ---
async def test_writes_require_owner_or_manager(staff_client):
    r = await staff_client.post("/api/store-ops/load", json={
        "lines": [{"article_id": 5, "amount": 1}]})
    assert r.status_code in (401, 403)


async def test_reads_require_auth(client):
    assert (await client.get("/api/store-ops/depots")).status_code == 401


# ---------------------------------------------------------------- Ревизия (stocktake) ---
async def test_revision_preview_computes_variance(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {"store_amount": 10})   # system on-hand = 10
    r = await owner_client.post("/api/store-ops/revision/preview", json={
        "lines": [{"article_id": 5, "counted": 7}, {"article_id": 6, "counted": 12}]})
    assert r.status_code == 200, r.text
    body = r.json()
    row0 = body["lines"][0]
    assert row0["article_id"] == 5 and row0["system"] == 10.0 and row0["variance"] == -3.0 and row0["known"]
    assert body["total_variance"] == -1.0       # (7-10) + (12-10) = -3 + 2
    assert body["shortage_lines"] == 1 and body["surplus_lines"] == 1 and body["unknown_lines"] == 0


async def test_revision_preview_flags_failed_read_not_zero(owner_client, barsy_recorder):
    def _boom(_body):
        raise RuntimeError("Barsy down")
    barsy_recorder.set_return("Articles_get", _boom)   # stock read fails
    r = await owner_client.post("/api/store-ops/revision/preview", json={
        "lines": [{"article_id": 5, "counted": 7}]})
    assert r.status_code == 200, r.text
    row = r.json()["lines"][0]
    assert row["known"] is False and row["system"] is None and row["variance"] is None
    assert r.json()["unknown_lines"] == 1        # NOT counted as a +7 phantom surplus


async def test_revision_post_dry_run_shape(owner_client, barsy_recorder):
    r = await owner_client.post("/api/store-ops/revision", json={
        "lines": [{"article_id": 5, "counted": 7}], "depot_id": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["method"] == "Revisions_save"
    assert body["barsy_body"]["action_type"] == "save_and_close"     # posts → adjusts stock
    assert body["barsy_body"]["values"]["depot_id"] == "1"
    assert body["barsy_body"]["rows"][0] == {"row_id": "1", "article_id": "5", "amount": "7"}


async def test_revision_requires_owner(staff_client):
    r = await staff_client.post("/api/store-ops/revision", json={"lines": [{"article_id": 5, "counted": 1}]})
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------- live-gate: correct method+body sent ---
async def test_writeoff_live_sends_storeouts_save(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Storeouts_save", 5)
    r = await owner_client.post("/api/store-ops/writeoff", json={
        "lines": [{"article_id": 7, "amount": 1}], "reason_id": 1, "confirm": True})
    assert r.status_code == 200 and r.json()["dry_run"] is False
    body = barsy_recorder.body_for("Storeouts_save")
    assert body["action_type"] == "save_and_close" and body["values"]["depot_id"] == "1"
    assert body["rows"][0]["article_id"] == "7"


async def test_transfer_live_sends_storemoves_create(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Depots_getlist", [{"depot_id": 1}, {"depot_id": 2}])
    barsy_recorder.set_return("Storemoves_create", 7)
    r = await owner_client.post("/api/store-ops/transfer", json={
        "lines": [{"article_id": 5, "amount": 2}], "depot_from": 1, "depot_to": 2, "confirm": True})
    assert r.status_code == 200 and r.json()["dry_run"] is False
    sm = barsy_recorder.body_for("Storemoves_create")["storemove"]
    assert sm["depot_id_left"] == 1 and sm["depot_id_right"] == 2


async def test_revision_live_sends_revisions_save(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    barsy_recorder.set_return("Revisions_save", 3)
    r = await owner_client.post("/api/store-ops/revision", json={
        "lines": [{"article_id": 5, "counted": 8}], "depot_id": 1, "confirm": True})
    assert r.status_code == 200 and r.json()["dry_run"] is False
    body = barsy_recorder.body_for("Revisions_save")
    assert body["action_type"] == "save_and_close" and body["rows"][0]["amount"] == "8"


# ---------------------------------------------------------------- confirm guard (live only) ---
async def test_live_write_without_confirm_is_400_and_writes_nothing(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(store_ops, "get_settings", _live_settings)
    r = await owner_client.post("/api/store-ops/revision", json={"lines": [{"article_id": 5, "counted": 1}]})
    assert r.status_code == 400
    assert "Revisions_save" not in barsy_recorder.operations   # gate-on but unconfirmed → no write
