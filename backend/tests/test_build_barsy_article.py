# -*- coding: utf-8 -*-
"""Unit tests for _build_barsy_article — the pure draft -> Barsy Articles_create mapping.

Imported and called directly (no DB, no HTTP). This encodes the verified Barsy contract,
so a regression here would silently corrupt every product pushed to the ERP.
"""
import json

from app.models import IntakeDraft
from app.routers.intake import _build_barsy_article, _resolve_cat_id


def _draft(**kw) -> IntakeDraft:
    kw.setdefault("attributes_json", "[]")
    return IntakeDraft(**kw)


def test_name_maps_to_article_name_and_ml():
    d = _draft(code="C", name="Керамична чаша")
    art = _build_barsy_article(d)["article"]
    assert art["article_name"] == "Керамична чаша"
    assert art["article_name_ml"] == {"bg_BG": "Керамична чаша"}


def test_known_unit_maps_to_amount_type_id():
    assert _build_barsy_article(_draft(code="C", name="N", unit="бр"))["article"]["amount_type_id"] == 1
    assert _build_barsy_article(_draft(code="C", name="N", unit="кг"))["article"]["amount_type_id"] == 3
    assert _build_barsy_article(_draft(code="C", name="N", unit="л"))["article"]["amount_type_id"] == 2


def test_unknown_unit_defaults_to_1():
    art = _build_barsy_article(_draft(code="C", name="N", unit="qux"))["article"]
    assert art["amount_type_id"] == 1


def test_barcode_sent_as_string_not_array():
    art = _build_barsy_article(_draft(code="C", name="N", barcode="3800130123456"))["article"]
    assert art["barcode"] == "3800130123456"
    assert isinstance(art["barcode"], str)  # Barsy create rejects the array form (500)


def test_barcode_omitted_when_empty():
    art = _build_barsy_article(_draft(code="C", name="N", barcode=""))["article"]
    assert "barcode" not in art


def test_price_passed_through_untouched():
    # price is staff-entered (Microinvest), never AI-touched — must not be reformatted
    art = _build_barsy_article(_draft(code="C", name="N", price="12.50"))["article"]
    assert art["actual_price"] == "12.50"


def test_price_omitted_when_empty():
    art = _build_barsy_article(_draft(code="C", name="N", price=""))["article"]
    assert "actual_price" not in art


def test_attributes_map_to_details_list():
    attrs = [{"key": "Материал", "value": "керамика"}, {"key": "Цвят", "value": "бял"}]
    d = _draft(code="C", name="N", attributes_json=json.dumps(attrs, ensure_ascii=False))
    art = _build_barsy_article(d)["article"]
    assert art["details"] == [
        {"detail_name": "Материал", "detail_value": "керамика"},
        {"detail_name": "Цвят", "detail_value": "бял"},
    ]


def test_no_details_key_when_no_attributes():
    art = _build_barsy_article(_draft(code="C", name="N"))["article"]
    assert "details" not in art


def test_fixed_flags_present():
    art = _build_barsy_article(_draft(code="C", name="N"))["article"]
    assert art["is_public"] == 1
    assert art["is_for_sale"] == 1
    assert art["code"] == "C"


# --- brand → "Марка" detail (Barsy has no native brand field; parity with barsy_push._details) ---
def test_brand_written_as_marka_detail():
    art = _build_barsy_article(_draft(code="C", name="N", brand="Luminarc"))["article"]
    assert {"detail_name": "Марка", "detail_value": "Luminarc"} in art["details"]


def test_brand_marka_is_prepended_before_attributes():
    attrs = [{"key": "Материал", "value": "стъкло"}]
    d = _draft(code="C", name="N", brand="Bormioli", attributes_json=json.dumps(attrs, ensure_ascii=False))
    art = _build_barsy_article(d)["article"]
    assert art["details"][0] == {"detail_name": "Марка", "detail_value": "Bormioli"}
    assert art["details"][1] == {"detail_name": "Материал", "detail_value": "стъкло"}


def test_no_marka_detail_when_brand_empty():
    art = _build_barsy_article(_draft(code="C", name="N", brand=""))["article"]
    assert "details" not in art


def test_brand_not_duplicated_if_attributes_already_carry_marka():
    attrs = [{"key": "Марка", "value": "ВЕЧЕ"}]
    d = _draft(code="C", name="N", brand="Друга", attributes_json=json.dumps(attrs, ensure_ascii=False))
    art = _build_barsy_article(d)["article"]
    marka = [x for x in art["details"] if x["detail_name"] == "Марка"]
    assert len(marka) == 1                       # no duplicate Марка row


# --- SEO meta → extra_data (native Barsy object; parity with barsy_push) ---
def test_seo_meta_written_to_extra_data():
    d = _draft(code="C", name="N", meta_title="Купа за салата", meta_description="Голяма керамична купа")
    art = _build_barsy_article(d)["article"]
    assert art["extra_data"] == {"seo_title": "Купа за салата", "seo_description": "Голяма керамична купа"}


def test_no_extra_data_when_no_meta():
    art = _build_barsy_article(_draft(code="C", name="N"))["article"]
    assert "extra_data" not in art


# --- oversell → sale_below_zero (backorder / supplier dropship) ---
def test_oversell_sets_sale_below_zero_yes():
    art = _build_barsy_article(_draft(code="C", name="N", oversell=True))["article"]
    assert art["sale_below_zero"] == "YES"


def test_no_sale_below_zero_when_not_oversell():
    art = _build_barsy_article(_draft(code="C", name="N"))["article"]
    assert "sale_below_zero" not in art          # leave Barsy's default (DEF)


# --- weight → extra_data.weight_kg (native Barsy object; parity with barsy_push) ---
def test_weight_written_to_extra_data():
    art = _build_barsy_article(_draft(code="C", name="N", weight_kg="0.55"))["article"]
    assert art["extra_data"]["weight_kg"] == "0.55"


def test_weight_and_seo_coexist_in_extra_data():
    d = _draft(code="C", name="N", weight_kg="1.2", meta_title="Заглавие")
    art = _build_barsy_article(d)["article"]
    assert art["extra_data"] == {"seo_title": "Заглавие", "weight_kg": "1.2"}


def test_no_weight_key_when_blank():
    art = _build_barsy_article(_draft(code="C", name="N"))["article"]
    assert "extra_data" not in art


# --- G6: Shopify leaf → Barsy cat_id resolution (set in _save_to_barsy, not the pure builder) ---
def test_resolve_cat_id_builds_gid_from_suffix(monkeypatch):
    """The picker stores the leaf SUFFIX; the checkpoint is keyed by full gid — so the suffix must be
    expanded to gid://shopify/TaxonomyCategory/<suffix> before lookup."""
    import barsy_push as bp
    seen = {}

    def fake(meta, ensure=False):
        seen["gid"] = meta["leaf_gid"]
        return 3803

    monkeypatch.setattr(bp, "_cat_id_for", fake)
    assert _resolve_cat_id("hg-1-2") == 3803
    assert seen["gid"] == "gid://shopify/TaxonomyCategory/hg-1-2"


def test_resolve_cat_id_passes_full_gid_through(monkeypatch):
    import barsy_push as bp
    got = {}

    def fake(meta, ensure=False):
        got["gid"] = meta["leaf_gid"]
        return 7

    monkeypatch.setattr(bp, "_cat_id_for", fake)
    _resolve_cat_id("gid://shopify/TaxonomyCategory/ap")
    assert got["gid"] == "gid://shopify/TaxonomyCategory/ap"


def test_resolve_cat_id_is_read_only(monkeypatch):
    """A product save must never create categories as a side effect (ensure must be False)."""
    import barsy_push as bp
    calls = {}

    def fake(meta, ensure=False):
        calls["ensure"] = ensure
        return 1

    monkeypatch.setattr(bp, "_cat_id_for", fake)
    _resolve_cat_id("ap")
    assert calls["ensure"] is False


def test_resolve_cat_id_empty_is_none():
    assert _resolve_cat_id("") is None
    assert _resolve_cat_id("   ") is None


def test_resolve_cat_id_never_raises(monkeypatch):
    import barsy_push as bp
    def boom(*a, **k):
        raise RuntimeError("checkpoint missing")
    monkeypatch.setattr(bp, "_cat_id_for", boom)
    assert _resolve_cat_id("ap") is None          # unknown/absent → uncategorised, not an error
