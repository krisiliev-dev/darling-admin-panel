# -*- coding: utf-8 -*-
"""Barsy „Клиенти" — the `clients` entity (page-style CRUD; Barsy is the system of record).

Captured live 2026-08-04 (read-only probe): list via `Clients_getListSmart`, groups via
`Clients_getGroups`, per-client values + write via the `clients_edit` page
(action_type "values" to read, "save" to write; active_struct_id "eStructForm_1"; key client_id),
delete via `Clients_delete{client_id}`.

`clients_edit` save is FULL-REPLACE: the body's `values` becomes the WHOLE client, so an edit must
read the current values first and overlay only what changed (the general Barsy law — a partial
body BLANKS fields; the users_edit incident is the precedent). Create builds a complete values dict
from the field defaults captured off the create form, so a new client is never half-built.
"""
from __future__ import annotations

from typing import Any

from ..core import barsy

STRUCT = "eStructForm_1"


class BarsyClientError(RuntimeError):
    pass


# The full clients_edit field set with Barsy's captured create-form defaults (probe 2026-08-04).
# Everything a create must send; panel inputs overlay a subset. Kept verbatim from the wire —
# allow_*_nocard default "1", ml_subscribed default -2, discount default 0 — rather than a
# cleaner shape Barsy has never seen.
_CREATE_DEFAULTS: dict = {
    "client_id": None, "client_name": None, "tel": None, "email": None, "client_auth": None,
    "group_id": None, "discount": 0, "client_code": None, "credit_limit": None,
    "paid_period": None, "allow_client_nocard": "1", "allow_credit_nocard": "1",
    "allow_discount_nocard": "1", "delete_flag": None, "barsys": None, "client_source_id": None,
    "recruit_client_name": None, "recom": None, "description": None, "lang_id": None,
    "agent_client_name": None, "gln": None, "edi_supplier_code": None, "company_name": None,
    "email_invoices": None, "bulstat": None, "vat_num": None, "mol": None, "receiver_name": None,
    "town": None, "address": None, "zip": None, "default_paymethod_id": None,
    "delivery_address": None, "ml_subscribed": -2, "export_id": None, "client_pass": None,
    "weight": None, "client_color": None,
}

# Fields the panel form is allowed to set. A guard, not decoration: it stops a caller from writing
# Barsy internals (barsys, client_pictures, client_id) through this surface, and — with
# exclude_unset in the router — from blanking a field it never showed the user.
EDITABLE_FIELDS = (
    "client_name", "tel", "email", "group_id", "discount", "credit_limit",
    "company_name", "bulstat", "vat_num", "mol", "email_invoices",
    "receiver_name", "town", "address", "zip", "description", "delete_flag",
)


def list_clients() -> list[dict]:
    r = barsy.call("Clients_getListSmart", {})
    return r if isinstance(r, list) else []


def groups() -> list[dict]:
    r = barsy.call("Clients_getGroups", {})
    return r if isinstance(r, list) else []


def read_values(client_id: int) -> dict:
    """The current full values for a client — the READ half of read-modify-write."""
    r = barsy.call("clients_edit", {"id": client_id, "action_type": "values",
                                    "active_struct_id": STRUCT, "params": {"id": client_id}})
    if isinstance(r, dict) and isinstance(r.get("values"), dict):
        return r["values"]
    return r if isinstance(r, dict) else {}


def _clean(inp: dict) -> dict:
    return {k: v for k, v in inp.items() if k in EDITABLE_FIELDS}


def create(inp: dict) -> int:
    values = dict(_CREATE_DEFAULTS)
    values.update(_clean(inp))
    values["client_id"] = None
    out = barsy.call("clients_edit", {"id": None, "action_type": "save",
                                      "active_struct_id": STRUCT, "values": values})
    return _extract_id(out)


def update(client_id: int, inp: dict) -> int:
    current = read_values(client_id)
    if not current:
        raise BarsyClientError(f"клиент {client_id} не е намерен")
    values = dict(current)
    values.update(_clean(inp))          # overlay ONLY the fields the caller explicitly set
    values["client_id"] = client_id
    out = barsy.call("clients_edit", {"id": client_id, "action_type": "save",
                                      "active_struct_id": STRUCT, "values": values})
    return _extract_id(out, default=client_id)


def delete(client_id: int) -> Any:
    return barsy.call("Clients_delete", {"client_id": client_id})


def _extract_id(out, default: int | None = None) -> int:
    """clients_edit save returns the new id (e.g. 40) — bare int, or wrapped under a key."""
    if isinstance(out, bool):        # guard: True/False is never an id
        out = None
    if isinstance(out, int):
        return out
    if isinstance(out, dict):
        for k in ("clients_edit", "id", "client_id"):
            v = out.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
    if default is not None:
        return default
    raise BarsyClientError(f"clients_edit save returned no id: {out!r}")
