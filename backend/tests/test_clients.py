# -*- coding: utf-8 -*-
"""Клиенти CRUD wires to Barsy's client entity (#79). These assert the BODIES sent to Barsy:
create builds a full values dict from captured defaults; edit is read-modify-write with untouched
fields preserved (clients_edit save is FULL-REPLACE); delete uses the client_id key; the surface
is owner-only.
"""
import pytest

GROUPS = [{"group_id": 1, "group_name": "Основна", "default_paymethod_id": 1}]
LIST = [
    {"client_id": 23, "client_name": "ЛИНЕКС", "tel": None, "email": None, "discount": "0", "group_id": 1},
    {"client_id": 40, "client_name": "Тест Клиент", "tel": "0888", "email": "t@x.bg", "discount": "0", "group_id": 1},
]
# what a values-read returns for client 23 — includes internals the panel does NOT expose
CURRENT_23 = {
    "client_id": 23, "client_name": "ЛИНЕКС", "tel": None, "email": None,
    "group_id": 1, "discount": "0", "credit_limit": None,
    "company_name": "ЛИНЕКС ЕООД", "bulstat": "104599826", "vat_num": "BG104599826",
    "mol": "ЛЮБОМИР", "town": None, "address": "адрес", "zip": None,
    "barsys": [7], "client_source_id": 2, "ml_subscribed": -2,
    "allow_client_nocard": 1, "delivery_address": {"x": 1}, "export_id": "MI-57",
}


def _clients_edit_dispatch(new_id):
    def _fn(body):
        if body.get("action_type") == "values":
            return dict(CURRENT_23)
        return new_id
    return _fn


def _save_body(rec):
    for op, body in rec.calls:
        if op == "clients_edit" and body.get("action_type") == "save":
            return body
    raise AssertionError(f"no clients_edit save; saw {rec.operations}")


@pytest.mark.asyncio
async def test_list_returns_barsy_clients(owner_client, barsy_recorder):
    barsy_recorder.set_return("Clients_getListSmart", LIST)
    r = await owner_client.get("/api/clients")
    assert r.status_code == 200, r.text
    assert [c["client_id"] for c in r.json()["clients"]] == [23, 40]


@pytest.mark.asyncio
async def test_groups_passthrough(owner_client, barsy_recorder):
    barsy_recorder.set_return("Clients_getGroups", GROUPS)
    r = await owner_client.get("/api/clients/groups")
    assert r.status_code == 200
    assert r.json()["groups"][0]["group_id"] == 1


@pytest.mark.asyncio
async def test_create_builds_full_values_with_defaults(owner_client, barsy_recorder):
    barsy_recorder.set_return("clients_edit", _clients_edit_dispatch(new_id=41))
    r = await owner_client.post("/api/clients", json={
        "client_name": "Нов Клиент", "tel": "0899123456", "email": "nov@x.bg",
        "group_id": 1, "discount": "5", "bulstat": "123456789"})
    assert r.status_code == 201, r.text
    assert r.json() == {"client_id": 41, "created": True}
    body = _save_body(barsy_recorder)
    assert body["id"] is None and body["active_struct_id"] == "eStructForm_1"
    v = body["values"]
    assert v["client_name"] == "Нов Клиент" and v["tel"] == "0899123456"
    assert v["discount"] == "5" and v["bulstat"] == "123456789"
    # captured Barsy defaults present, so a full-replace create is never half-built
    assert v["allow_client_nocard"] == "1" and v["ml_subscribed"] == -2
    assert v["client_id"] is None


@pytest.mark.asyncio
async def test_update_is_read_modify_write_preserving_untouched(owner_client, barsy_recorder):
    barsy_recorder.set_return("clients_edit", _clients_edit_dispatch(new_id=23))
    # change only the phone; everything else must survive (full-replace rule)
    r = await owner_client.put("/api/clients/23", json={
        "client_name": "ЛИНЕКС", "tel": "0700700700", "group_id": 1, "discount": "0"})
    assert r.status_code == 200, r.text
    body = _save_body(barsy_recorder)
    assert body["id"] == 23
    v = body["values"]
    assert v["tel"] == "0700700700"                  # the change
    assert v["bulstat"] == "104599826"               # preserved from the read
    assert v["barsys"] == [7]                         # an internal the panel never exposes — kept
    assert v["delivery_address"] == {"x": 1}          # kept verbatim
    assert v["client_id"] == 23


@pytest.mark.asyncio
async def test_delete_uses_client_id_key(owner_client, barsy_recorder):
    barsy_recorder.set_return("Clients_delete", {"ok": 1})
    r = await owner_client.delete("/api/clients/40")
    assert r.status_code == 200
    assert barsy_recorder.body_for("Clients_delete") == {"client_id": 40}


@pytest.mark.asyncio
async def test_create_requires_a_name(owner_client, barsy_recorder):
    r = await owner_client.post("/api/clients", json={"client_name": "   "})
    assert r.status_code == 400
    assert "clients_edit" not in barsy_recorder.operations   # nothing sent to Barsy


@pytest.mark.asyncio
async def test_non_owner_cannot_write(packer_client, barsy_recorder):
    r = await packer_client.post("/api/clients", json={"client_name": "X"})
    assert r.status_code in (401, 403)
    assert "clients_edit" not in barsy_recorder.operations
