# -*- coding: utf-8 -*-
"""Unit tests for barsy_auth.map_role — the pure Barsy-role -> panel-role mapping.

Called directly (no DB, no HTTP, no Barsy). This is what decides whether a Barsy user who
logs into the panel gets full access (is_superuser) or a limited role, so a regression here
is a privilege-escalation / lock-out risk.
"""
from app.barsy_auth import map_role


def _profile(role_name):
    return {"user_id": 7, "role_name": role_name}


# --- management roles → manager + superuser ----------------------------------------
def test_manager_bg_maps_to_manager_superuser():
    assert map_role(_profile("Мениджър")) == ("manager", True)


def test_director_bg_maps_to_manager_superuser():
    assert map_role(_profile("Управител")) == ("manager", True)


def test_management_is_case_and_whitespace_insensitive():
    # map_role lowercases + strips, so surrounding spaces / case must not matter
    assert map_role(_profile("  МЕНИДЖЪР  ")) == ("manager", True)


# --- staff role → staff, not superuser ---------------------------------------------
def test_employee_bg_maps_to_staff():
    assert map_role(_profile("Служител")) == ("staff", False)


# --- operator role → packer, not superuser -----------------------------------------
def test_operator_bg_maps_to_packer():
    assert map_role(_profile("Оператор")) == ("packer", False)


# --- unknown / missing → FAIL-CLOSED to packer (least privilege), never superuser ----
def test_unknown_role_defaults_to_packer():
    assert map_role(_profile("Счетоводител")) == ("packer", False)


def test_missing_role_name_defaults_to_packer():
    assert map_role({"user_id": 1}) == ("packer", False)


def test_empty_role_name_defaults_to_packer():
    assert map_role(_profile("")) == ("packer", False)
