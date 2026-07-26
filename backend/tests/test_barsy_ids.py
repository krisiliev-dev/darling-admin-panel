# -*- coding: utf-8 -*-
"""The single Barsy-response id parser (consolidated from 3 near-duplicate helpers)."""
from app.barsy_ids import extract_id


def test_bare_int_and_digit_string():
    assert extract_id(460) == 460
    assert extract_id("465") == 465
    assert extract_id("  12 ") == 12


def test_dict_keys_in_order():
    assert extract_id({"article_id": 7}) == 7
    assert extract_id({"id": 9}) == 9
    assert extract_id({"article_id": "11"}) == 11
    # storeload responses only resolve when storeload_id is in the requested key set
    assert extract_id({"storeload_id": 3}) is None
    assert extract_id({"storeload_id": 3}, ("article_id", "storeload_id", "id")) == 3


def test_unparseable_returns_none():
    assert extract_id(None) is None
    assert extract_id("abc") is None
    assert extract_id({"other": 1}) is None
    assert extract_id({"article_id": "x"}) is None
