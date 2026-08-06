# -*- coding: utf-8 -*-
"""Каталог (/api/catalog) — browse + edit products straight from Barsy.

Barsy is the system of record; we mock the shared barsy.call so nothing hits the real ERP.
The load-bearing contract under test is the Articles_update body mapping: name → article_name
+ article_name_ml, description → description_ml, barcode as a STRING (Barsy rejects the array
form), and price is NEVER written from here (price policy — Microinvest is the source).

Equally load-bearing is the create/edit parity restored on top of it: name_public →
article_name_public (the storefront title the connector prefers), status → the is_for_sale /
is_public pair that drives the Shopify auto-sync, brand + attributes → ONE details list Barsy
replaces wholesale, and SEO/weight MERGED into extra_data. Blank/None/[] means leave unchanged.
"""


# --- read paths: any authenticated user may browse ---------------------------------
async def test_list_articles_readable_by_staff(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "SKU-1", "article_name": "Чаша", "actual_price": "5.00"},
        {"article_id": 2, "code": "SKU-2", "article_name": "Чиния", "actual_price": "8.00"},
    ])
    r = await staff_client.get("/api/catalog")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["article_id"] for i in items] == [1, 2]
    # core fields + stock folded in (None when Barsy stock is empty)
    assert {k: items[0][k] for k in ("article_id", "code", "name", "price")} == \
        {"article_id": 1, "code": "SKU-1", "name": "Чаша", "price": "5.00"}
    assert items[0]["stock"] is None and items[0]["low"] is False
    assert barsy_recorder.operations == ["Articles_getlist", "Store_amounts"]  # stock join


async def test_catalog_folds_stock_and_low_filter(staff_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "A", "article_name": "Чаша", "actual_price": "5.00"},
        {"article_id": 2, "code": "B", "article_name": "Чиния", "actual_price": "8.00"},
    ])
    barsy_recorder.set_return("Store_amounts", {"rows": [
        {"article_id": 1, "store_amount": "2", "min_amount": "5", "below_min": 1},   # low
        {"article_id": 2, "store_amount": "50", "min_amount": "5"},                   # ok
    ]})
    items = (await staff_client.get("/api/catalog")).json()["items"]
    by_id = {i["article_id"]: i for i in items}
    assert by_id[1]["stock"] == 2 and by_id[1]["low"] is True
    assert by_id[2]["stock"] == 50 and by_id[2]["low"] is False
    # low_only filter keeps just the low one
    low = (await staff_client.get("/api/catalog?low_only=true")).json()["items"]
    assert [i["article_id"] for i in low] == [1]


async def test_product_image_404_when_no_image(staff_client, barsy_recorder):
    # no files for the article → 404 → frontend shows a placeholder (unique id avoids lru cache clash)
    barsy_recorder.set_return("Files_getlist", [])
    r = await staff_client.get("/api/catalog/image/99913?size=thumb")
    assert r.status_code == 404


async def test_list_articles_readable_by_packer(packer_client, barsy_recorder):
    # browse is gated by current_active_user only — even the lowest role may read
    barsy_recorder.set_return("Articles_getlist", [])
    r = await packer_client.get("/api/catalog")
    assert r.status_code == 200


async def test_get_article_collapses_barcode_list(owner_client, barsy_recorder):
    # Barsy may return barcode as a list; the detail view collapses it to the first value
    barsy_recorder.set_return("Articles_get", {
        "article_id": 55, "code": "SKU-55", "article_name": "Кана",
        "description": "описание", "actual_price": "12.50",
        "barcode": ["3800130123456", "3800130999999"],
    })
    r = await owner_client.get("/api/catalog/55")
    assert r.status_code == 200, r.text
    assert r.json()["barcode"] == "3800130123456"


async def test_get_article_reads_back_every_editable_barsy_field(owner_client, barsy_recorder):
    # create/edit parity: the detail view must expose everything intake writes on create, or the
    # editor silently drops it (article_name_public was permanently null before this)
    barsy_recorder.set_return("Articles_get", {
        "article_id": 55, "code": "SKU-55", "article_name": "Кана",
        "article_name_public": "Керамична кана 1.5л",
        "description_public_ml": {"bg_BG": "Публично описание"},
        "article_details": {"1": {"key": "Марка", "value": "Luminarc"},
                            "2": {"key": "Материал", "value": "стъкло"}},
        "master_cat_id": 42, "cat_id": 7, "amount_type_id": 3,
        "extra_data": {"seo_title": "SEO заглавие", "seo_description": "SEO описание", "weight_kg": "1.2"},
        "is_for_sale": 1, "is_public": 1,
        "ref_num": "DL-2026-14", "article_aliases": ["кана", "кани"],
    })
    d = (await owner_client.get("/api/catalog/55")).json()
    assert d["name_public"] == "Керамична кана 1.5л"
    assert d["description_public"] == "Публично описание"
    assert d["brand"] == "Luminarc"                        # the Марка row, lifted out
    assert d["attributes"] == [{"key": "Материал", "value": "стъкло"}]   # …and excluded from the rest
    assert d["cat_id"] == 42 and d["amount_type_id"] == 3   # master_cat_id wins over cat_id
    assert d["seo_title"] == "SEO заглавие" and d["seo_description"] == "SEO описание"
    assert d["is_for_sale"] is True and d["is_public"] is True
    assert d["ref_num"] == "DL-2026-14" and d["aliases"] == ["кана", "кани"]
    # article_details must be requested explicitly or Barsy omits brand/attributes
    assert barsy_recorder.body_for("Articles_get")["extra_properties"] == \
        {"extra_data": 1, "article_details": 1}


async def test_get_article_defaults_new_fields_when_absent(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Гола кана"})
    d = (await owner_client.get("/api/catalog/55")).json()
    assert d["name_public"] == "" and d["description_public"] == "" and d["brand"] == ""
    assert d["attributes"] == [] and d["aliases"] == [] and d["ref_num"] == ""
    assert d["cat_id"] is None and d["amount_type_id"] is None


async def test_get_article_derives_status(owner_client, barsy_recorder):
    # status drives the Shopify auto-sync (ACTIVE / Draft / Archive)
    for for_sale, public, expected in [(1, 1, "active"), (1, 0, "draft"),
                                       (0, 0, "withdrawn"), (0, 1, "withdrawn")]:
        barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Кана",
                                                   "is_for_sale": for_sale, "is_public": public})
        d = (await owner_client.get("/api/catalog/55")).json()
        assert d["status"] == expected, (for_sale, public)


async def test_get_missing_article_is_404(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {})  # no article_id → not found
    r = await owner_client.get("/api/catalog/999")
    assert r.status_code == 404


async def test_list_articles_requires_auth(client):
    r = await client.get("/api/catalog")
    assert r.status_code == 401


# --- write path: Articles_update body mapping --------------------------------------
async def test_update_body_maps_fields_and_omits_price(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/55", json={
        "name": "  Нова кана  ",
        "description": "Голяма керамична кана",
        "barcode": "3800130123456",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    body = barsy_recorder.body_for("Articles_update")
    # article_id lives at the TOP level of the update body
    assert body["article_id"] == 55
    art = body["article"]
    # name → article_name (+ trimmed) and multilingual bg_BG
    assert art["article_name"] == "Нова кана"
    assert art["article_name_ml"] == {"bg_BG": "Нова кана"}
    # description → description_ml
    assert art["description_ml"] == {"bg_BG": "Голяма керамична кана"}
    # barcode is a plain STRING, never an array
    assert art["barcode"] == "3800130123456"
    assert isinstance(art["barcode"], str)
    # price is read-only here — the update must NOT carry any price key
    assert "actual_price" not in art
    assert "price" not in art


async def test_update_omits_optional_fields_when_blank(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Само име"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["article_name"] == "Само име"
    assert "description_ml" not in art
    assert "barcode" not in art
    assert art["sale_below_zero"] == "NO"          # explicit NO by default (toggle off)


async def test_update_sets_sale_below_zero_yes_when_oversell(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Дропшип", "oversell": True})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("Articles_update")["article"]["sale_below_zero"] == "YES"


async def test_update_merges_weight_into_extra_data_preserving_seo(staff_client, barsy_recorder):
    # existing article already has SEO in extra_data — the weight write must MERGE, not clobber it
    barsy_recorder.set_return("Articles_get", {"article_id": 7, "extra_data": {"seo_title": "Старо"}})
    r = await staff_client.post("/api/catalog/7", json={"name": "Тежък", "weight_kg": " 1.2 "})
    assert r.status_code == 200, r.text
    extra = barsy_recorder.body_for("Articles_update")["article"]["extra_data"]
    assert extra == {"seo_title": "Старо", "weight_kg": "1.2"}   # SEO preserved, weight added


async def test_update_maps_public_name_and_description(staff_client, barsy_recorder):
    # article_name_public is what the connector PREFERS for the Shopify storefront title
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "name_public": "  Керамична кана 1.5л  ",
        "description_public": "Публично описание"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["article_name_public"] == "Керамична кана 1.5л"
    assert art["description_public_ml"] == {"bg_BG": "Публично описание"}


async def test_update_status_draft_sends_both_flags(staff_client, barsy_recorder):
    # draft = sellable at the till, hidden online → Shopify Draft
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "draft"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["is_for_sale"] == 1 and art["is_public"] == 0


def _with_picture(rec, url="http://barsy/img/7.jpg"):
    """Stub an article that HAS a photo. «Активен» is the status that publishes a product and
    the panel refuses it without one, so any test that expects the flags to be written has to
    say which of the two worlds it is in."""
    rec.set_return("Articles_get", {"article_id": 7, "code": "7001"})
    rec.set_return("Articles_getlist", [{"article_id": 7, "code": "7001", "picture_url": url}])
    return rec


async def test_update_status_active_and_withdrawn_flags(staff_client, barsy_recorder):
    _with_picture(barsy_recorder)
    await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "active"})
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert (art["is_for_sale"], art["is_public"]) == (1, 1)
    barsy_recorder.calls.clear()
    await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "withdrawn"})
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert (art["is_for_sale"], art["is_public"]) == (0, 0)


async def test_publishing_without_a_picture_is_refused(staff_client, barsy_recorder):
    """The owner's rule: nothing reaches the storefront without a photo. Refused BEFORE the
    ERP is touched — a write that is going to be wrong should not happen and then be undone."""
    _with_picture(barsy_recorder, url="")
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "active"})
    assert r.status_code == 400, r.text
    assert "снимка" in r.json()["detail"]
    assert "Articles_update" not in barsy_recorder.operations


async def test_a_failed_picture_check_refuses_with_a_different_reason(staff_client, barsy_recorder):
    """Barsy not answering is not the same fact as the product having no photo, and the two
    send an operator to different places. Both fail closed; the messages must differ."""
    barsy_recorder.set_return("Articles_get", {"article_id": 7, "code": "7001"})
    barsy_recorder.set_return("Articles_getlist", None)      # unreadable answer
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "active"})
    assert r.status_code == 400, r.text
    from app.routers import catalog as _cat
    assert r.json()["detail"] == _cat._IMAGE_UNKNOWN
    assert "Articles_update" not in barsy_recorder.operations


async def test_taking_a_product_off_the_site_never_needs_a_picture(staff_client, barsy_recorder):
    """The remedy must always work: «Черновa» and «Изтеглен» un-publish, and a product with no
    photo is exactly the one you need to be able to pull down."""
    for st in ("draft", "withdrawn"):
        barsy_recorder.calls.clear()
        r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": st})
        assert r.status_code == 200, r.text
        assert barsy_recorder.body_for("Articles_update")["article"]["is_public"] == 0


async def test_update_without_status_sends_neither_flag(staff_client, barsy_recorder):
    # "" must not touch the sale/publish state — that would un-publish products on every edit
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert "is_for_sale" not in art and "is_public" not in art


async def test_update_invalid_status_is_400(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "status": "archived"})
    assert r.status_code == 400
    assert barsy_recorder.calls == []


async def test_update_brand_and_attributes_are_one_details_list(staff_client, barsy_recorder):
    # Barsy REPLACES details wholesale, so brand + attributes travel together, Марка first
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "brand": " Luminarc ",
        "attributes": [{"key": "Материал", "value": "стъкло"}, {"key": "Цвят", "value": "бял"}]})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("Articles_update")["article"]["details"] == [
        {"detail_name": "Марка", "detail_value": "Luminarc"},
        {"detail_name": "Материал", "detail_value": "стъкло"},
        {"detail_name": "Цвят", "detail_value": "бял"},
    ]


async def test_update_brand_not_duplicated_when_attributes_carry_marka(staff_client, barsy_recorder):
    # the read-back hands attributes and brand separately; if a Марка row rides back in the
    # attribute list it must still yield exactly one, first row
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "attributes": [{"key": "Материал", "value": "стъкло"},
                                       {"key": "Марка", "value": "Bormioli"}]})
    assert r.status_code == 200, r.text
    details = barsy_recorder.body_for("Articles_update")["article"]["details"]
    assert details[0] == {"detail_name": "Марка", "detail_value": "Bormioli"}
    assert [d["detail_name"] for d in details].count("Марка") == 1


async def test_update_omits_details_when_neither_brand_nor_attributes(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана"})
    assert r.status_code == 200, r.text
    assert "details" not in barsy_recorder.body_for("Articles_update")["article"]


async def test_update_maps_category_and_unit(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "cat_id": 42,
                                                        "amount_type_id": 3})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["cat_id"] == 42 and art["master_cat_id"] == 42   # both, same value
    assert art["amount_type_id"] == 3


async def test_update_blank_optionals_leave_barsy_untouched(staff_client, barsy_recorder):
    # "" / None / [] means LEAVE UNCHANGED — an unedited field must never clobber Barsy
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "name_public": "", "description_public": "", "brand": "",
        "attributes": [], "cat_id": None, "amount_type_id": None,
        "seo_title": "", "seo_description": "", "status": "", "weight_kg": ""})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    for k in ("article_name_public", "description_public_ml", "details", "cat_id", "master_cat_id",
              "amount_type_id", "extra_data", "is_for_sale", "is_public"):
        assert k not in art, k
    assert barsy_recorder.operations.count("Articles_get") == 1   # one read to merge the ml maps


async def test_update_merges_seo_and_weight_into_extra_data_in_one_read(staff_client, barsy_recorder):
    # extra_data also holds variant_codes — merge, never replace; and read it only ONCE
    barsy_recorder.set_return("Articles_get", {
        "article_id": 7, "extra_data": {"variant_codes": ["A1"], "seo_description": "Старо"}})
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "seo_title": " Ново заглавие ", "weight_kg": "1.2"})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("Articles_update")["article"]["extra_data"] == {
        "variant_codes": ["A1"], "seo_description": "Старо",     # pre-existing keys survive
        "seo_title": "Ново заглавие", "weight_kg": "1.2"}
    assert barsy_recorder.operations.count("Articles_get") == 1


async def test_update_empty_name_is_400(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "   "})
    assert r.status_code == 400
    assert barsy_recorder.calls == []  # never reached the ERP


async def test_update_refuses_non_staff(packer_client, barsy_recorder):
    # packer is below 'staff' and is not a superuser → require_roles('staff') refuses
    r = await packer_client.post("/api/catalog/7", json={"name": "Опит"})
    assert r.status_code == 403
    assert barsy_recorder.calls == []


async def test_update_allows_owner_superuser(owner_client, barsy_recorder):
    # a superuser (owner/manager) always passes require_roles
    r = await owner_client.post("/api/catalog/7", json={"name": "От собственик"})
    assert r.status_code == 200, r.text
    assert barsy_recorder.operations == ["Articles_get", "Articles_update"]


async def test_update_requires_auth(client, barsy_recorder):
    r = await client.post("/api/catalog/7", json={"name": "Х"})
    assert r.status_code == 401
    assert barsy_recorder.calls == []


# --- accounting / stock-behaviour block ---------------------------------------------
# A Barsy field with no panel control is unmanageable AND invisible — staff can't tell it exists.
# These are the remaining real article-form fields: ДДС група, партиди, разфасовка, лимити,
# отстъпка/печалба, цена във втора валута, клиентска заявка + the two TRI-state below-zero flags.
async def test_get_article_reads_back_accounting_fields(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {
        "article_id": 55, "article_name": "Кана",
        "tax_id": 4, "lot_type_id": 3, "default_package_id": 11,
        "sale_below_zero": "YES", "storemove_below_zero": "NO",
        "max_discount": "15", "min_profit_percent": "12.5", "avg_delivery_price": "7.40",
        "part_factor": "0.5", "min_amount": "2", "max_amount": "100",
        "is_for_client_order": 1,
        # Barsy's OWN typo — reading the "corrected" spelling would return null forever
        "actuial_price_sec_currency": "19.99",
        "composite_type_name": "стандартен артикул", "recipe_description": "няма рецепта",
    })
    d = (await owner_client.get("/api/catalog/55")).json()
    assert d["tax_id"] == 4 and d["lot_type_id"] == 3 and d["default_package_id"] == 11
    assert d["oversell_mode"] == "YES" and d["storemove_mode"] == "NO"
    assert d["oversell"] is True                      # legacy boolean kept for backwards compat
    assert d["max_discount"] == 15 and d["min_profit_percent"] == 12.5
    assert d["avg_delivery_price"] == 7.40 and d["part_factor"] == 0.5
    assert d["min_amount"] == 2 and d["max_amount"] == 100
    assert d["is_for_client_order"] is True
    assert d["price_sec_currency"] == 19.99
    # read-only display (Комплектация is parked — semantics unverified)
    assert d["composite_type_name"] == "стандартен артикул"
    assert d["recipe_description"] == "няма рецепта"


async def test_get_article_defaults_accounting_fields_when_absent(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Гола кана",
                                               "default_package_id": ""})   # "" = без разфасовка
    d = (await owner_client.get("/api/catalog/55")).json()
    for k in ("tax_id", "lot_type_id", "default_package_id", "max_discount", "min_profit_percent",
              "avg_delivery_price", "part_factor", "min_amount", "max_amount", "price_sec_currency"):
        assert d[k] is None, k
    # absent below-zero flags mean "Според правата" — never a fabricated hard YES/NO
    assert d["oversell_mode"] == "DEF" and d["storemove_mode"] == "DEF"
    assert d["oversell"] is False
    assert d["is_for_client_order"] is False
    assert d["composite_type_name"] == "" and d["recipe_description"] == ""


async def test_get_article_default_package_zero_is_null(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Кана",
                                               "default_package_id": 0})
    assert (await owner_client.get("/api/catalog/55")).json()["default_package_id"] is None


async def test_update_maps_accounting_fields_to_barsy(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "tax_id": 4, "lot_type_id": 2, "default_package_id": 6,
        "max_discount": 15, "min_profit_percent": 12.5, "avg_delivery_price": 7.4,
        "part_factor": 0.5, "min_amount": 2, "max_amount": 100,
        "is_for_client_order": True, "price_sec_currency": 19.99})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert art["tax_id"] == 4 and art["lot_type_id"] == 2 and art["default_package_id"] == 6
    assert art["max_discount"] == 15 and art["min_profit_percent"] == 12.5
    assert art["avg_delivery_price"] == 7.4 and art["part_factor"] == 0.5
    assert art["min_amount"] == 2 and art["max_amount"] == 100
    assert art["is_for_client_order"] == 1
    # the typo'd key is what Barsy actually stores — a "corrected" key drops the value silently
    assert art["actuial_price_sec_currency"] == 19.99
    assert "actual_price_sec_currency" not in art


async def test_update_tri_state_round_trips_all_three_values(staff_client, barsy_recorder):
    # DEF ("Според правата") was unreachable while the panel wrote only YES/NO
    for mode in ("DEF", "YES", "NO"):
        barsy_recorder.calls.clear()
        r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "oversell_mode": mode,
                                                            "storemove_mode": mode})
        assert r.status_code == 200, r.text
        art = barsy_recorder.body_for("Articles_update")["article"]
        assert art["sale_below_zero"] == mode, mode
        assert art["storemove_below_zero"] == mode, mode


async def test_update_legacy_oversell_bool_still_writes_yes_no(staff_client, barsy_recorder):
    # back-compat: an older client sends only the boolean → unchanged YES/NO behaviour
    await staff_client.post("/api/catalog/7", json={"name": "Кана", "oversell": True})
    assert barsy_recorder.body_for("Articles_update")["article"]["sale_below_zero"] == "YES"
    barsy_recorder.calls.clear()
    await staff_client.post("/api/catalog/7", json={"name": "Кана", "oversell": False})
    assert barsy_recorder.body_for("Articles_update")["article"]["sale_below_zero"] == "NO"


async def test_update_oversell_mode_wins_over_legacy_bool(staff_client, barsy_recorder):
    r = await staff_client.post("/api/catalog/7", json={"name": "Кана", "oversell": True,
                                                        "oversell_mode": "DEF"})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("Articles_update")["article"]["sale_below_zero"] == "DEF"


async def test_update_blank_accounting_optionals_send_nothing(staff_client, barsy_recorder):
    # no-clobber: an unedited numeric field must not travel — a bare 0 would zero real settings
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "tax_id": None, "lot_type_id": None, "default_package_id": None,
        "max_discount": None, "min_profit_percent": None, "avg_delivery_price": None,
        "part_factor": None, "min_amount": None, "max_amount": None,
        "price_sec_currency": None, "is_for_client_order": None,
        "oversell_mode": "", "storemove_mode": ""})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    for k in ("tax_id", "lot_type_id", "default_package_id", "max_discount", "min_profit_percent",
              "avg_delivery_price", "part_factor", "min_amount", "max_amount",
              "actuial_price_sec_currency", "is_for_client_order", "storemove_below_zero"):
        assert k not in art, k
    assert art["sale_below_zero"] == "NO"        # legacy default path is untouched


async def test_update_invalid_tri_state_is_400(staff_client, barsy_recorder):
    for payload in ({"oversell_mode": "MAYBE"}, {"storemove_mode": "1"}):
        r = await staff_client.post("/api/catalog/7", json={"name": "Кана", **payload})
        assert r.status_code == 400, payload
    assert barsy_recorder.calls == []            # never reached the ERP


async def test_update_never_writes_composite_or_recipe(staff_client, barsy_recorder):
    # read-back only: Комплектация is parked, so writing recipe fields could corrupt a real recipe
    r = await staff_client.post("/api/catalog/7", json={
        "name": "Кана", "composite_type_name": "рецепта", "recipe_description": "1 x нещо"})
    assert r.status_code == 200, r.text
    art = barsy_recorder.body_for("Articles_update")["article"]
    assert "composite_type_name" not in art and "recipe_description" not in art


async def test_default_package_can_be_CLEARED_with_the_zero_sentinel(owner_client, barsy_recorder):
    """«Без разфасовка» is a real Barsy VALUE (empty string), not an absence — so "not edited" and
    "cleared" must be distinguishable or a разфасовка could never be removed from the panel. 0 isn't a
    valid package id (they're 1/6/11/16), so it's the explicit CLEAR sentinel."""
    barsy_recorder.set_return("Articles_update", {"article_id": 5001})
    r = await owner_client.post("/api/catalog/5001",
                                json={"name": "Продукт", "default_package_id": 0})
    assert r.status_code == 200, r.text
    assert barsy_recorder.body_for("Articles_update")["article"]["default_package_id"] == ""


async def test_omitting_default_package_leaves_it_untouched(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_update", {"article_id": 5001})
    await owner_client.post("/api/catalog/5001", json={"name": "Продукт"})
    assert "default_package_id" not in barsy_recorder.body_for("Articles_update")["article"]


async def test_get_article_prefers_ml_name(owner_client, barsy_recorder):
    # #100: the panel reads article_name_ml[bg_BG] with fallback to the scalar article_name
    barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Scalar",
                                               "article_name_ml": {"bg_BG": "МЛ Име", "en_US": "EN Name"}})
    d = (await owner_client.get("/api/catalog/55")).json()
    assert d["name"] == "МЛ Име"                                   # ml.bg_BG wins over the scalar
    barsy_recorder.set_return("Articles_get", {"article_id": 55, "article_name": "Само скалар"})
    d = (await owner_client.get("/api/catalog/55")).json()
    assert d["name"] == "Само скалар"                             # falls back when no ml
