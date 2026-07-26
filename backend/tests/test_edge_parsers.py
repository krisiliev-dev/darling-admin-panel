# -*- coding: utf-8 -*-
"""EXHAUSTIVE edge-case tests for the pure/parsing functions of the Дарлинг admin panel.

These target the fiddly string/number parsers where subtle bugs hide: courier payout-CSV
parsing, EAN validation, phone normalization, Decimal price rounding, weight coercion.

All functions here are pure — imported and called directly (no DB/fixtures/HTTP). `hypothesis`
is not installed in this environment, so these are hand-crafted edge tables rather than
property-based tests.

Two REAL bugs are pinned with @pytest.mark.xfail (source left untouched):
  * cod._parse_payout picks a "Номер на клиент" (client-number) column as the tracking column
    when it precedes the real "Товарителница" column.
  * cod._parse_payout picks a "Платец" (payer) column as the amount column when that payer
    field holds a numeric id (the _col_has_numbers guard passes on it).
"""
import pytest

from app import nekorekten, quality
from app.routers import cod, orders, pricing


# =====================================================================================
# cod._parse_amount — currency/decimal/thousands coercion
# =====================================================================================
class TestParseAmount:
    @pytest.mark.parametrize("raw, expected", [
        # plain decimals
        ("25.50", 25.5),
        ("19,99", 19.99),        # comma-decimal
        ("42", 42.0),            # integer
        ("0", 0.0),
        ("0.00", 0.0),
        # thousands separators — rightmost separator is the decimal point
        ("1.234,56", 1234.56),   # EU style
        ("1,234.56", 1234.56),   # US style
        ("1.234.567,89", 1234567.89),
        ("1,234,567.89", 1234567.89),
        # currency suffix / prefix / text noise stripped
        ("1.234,56 лв.", 1234.56),
        ("лв. 19,99", 19.99),
        ("25.50 EUR", 25.5),
        ("  42.00  ", 42.0),
        ("€25.50", 25.5),
        # negatives (deduction lines)
        ("-5.00", -5.0),
        ("-1.234,56", -1234.56),
        # huge
        ("999999999.99", 999999999.99),
        ("1000000.00", 1000000.0),
    ])
    def test_valid_amounts(self, raw, expected):
        assert cod._parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", None, "—", "abc", "лв.", ".", ",", "-", ".,", "-.", " ", "N/A",
    ])
    def test_unparseable_returns_none(self, raw):
        assert cod._parse_amount(raw) is None

    @pytest.mark.parametrize("raw, expected", [
        # a bare grouped value is genuinely ambiguous; the algorithm treats the rightmost
        # separator as the decimal point, so "1,234" -> 1.23 (documented behavior).
        ("1,234", 1.23),
        ("1.234", 1.23),
        # trailing / leading stray separators are stripped before parsing
        ("1.234,", 1.23),
        (".5", 5.0),
        # more than two separators: all but the rightmost are thousands
        ("1.2.3", 12.3),
    ])
    def test_ambiguous_documented_behavior(self, raw, expected):
        assert cod._parse_amount(raw) == expected

    def test_numeric_input_not_string(self):
        # non-string inputs are stringified first
        assert cod._parse_amount(25.5) == 25.5
        assert cod._parse_amount(42) == 42.0
        assert cod._parse_amount(0) is None  # str(0 or "") -> "" -> None (0 is falsy)

    def test_rounds_to_two_places(self):
        assert cod._parse_amount("1.239") == 1.24
        assert cod._parse_amount("1.234") == 1.23


# =====================================================================================
# cod._norm_tracking — keep digits only
# =====================================================================================
class TestNormTracking:
    @pytest.mark.parametrize("raw, expected", [
        (" BG 123-456 ", "123456"),
        ("1234567890", "1234567890"),
        ("  0151086  ", "0151086"),
        ("AWB#000-111-222", "000111222"),
        ("", ""),
        (None, ""),
        ("no digits here", ""),
        # NOTE: Python's \d matches Unicode digits, so Arabic-Indic digits are NOT stripped
        # (they survive as-is). Couriers use ASCII digits, so this is a harmless quirk.
        ("٤٢", "٤٢"),
    ])
    def test_norm_tracking(self, raw, expected):
        assert cod._norm_tracking(raw) == expected

    def test_leading_zeros_preserved(self):
        # couriers zero-pad; leading zeros are significant for the match key
        assert cod._norm_tracking("00042") == "00042"

    def test_numeric_zero_input_is_falsy(self):
        # str(0 or "") == "" — integer 0 becomes empty, but the parser passes strings
        assert cod._norm_tracking(0) == ""
        assert cod._norm_tracking(123) == "123"


# =====================================================================================
# cod._decode — encoding sniffing (utf-8-sig / cp1251 / latin-1)
# =====================================================================================
class TestDecode:
    def test_plain_utf8(self):
        assert cod._decode(b"hello,42") == "hello,42"

    def test_utf8_bom_stripped(self):
        raw = b"\xef\xbb\xbf" + "Товарителница,Сума".encode()
        out = cod._decode(raw)
        assert out.startswith("Товарителница")
        assert "﻿" not in out  # BOM removed by utf-8-sig

    def test_cp1251_cyrillic(self):
        # cp1251 Cyrillic bytes that are NOT valid utf-8 must fall through to cp1251
        raw = "Товарителница,Наложен платеж".encode("cp1251")
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")  # sanity: genuinely not utf-8
        out = cod._decode(raw)
        assert "Товарителница" in out and "Наложен платеж" in out

    def test_never_raises_on_arbitrary_bytes(self):
        # latin-1 decodes any byte, so _decode always returns a str
        assert isinstance(cod._decode(b"\x00\x81\xff\xfe"), str)


# =====================================================================================
# cod._parse_payout — delimiter sniff, header detection, decoy columns, ragged rows
# =====================================================================================
class TestParsePayout:
    def test_comma_bg_headers(self):
        p = cod._parse_payout("Товарителница,Наложен платеж\n1234567890,25.50\n")
        assert p["delimiter"] == ","
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_semicolon_comma_decimal(self):
        p = cod._parse_payout("awb;cod\n0151086;19,99\n")
        assert p["delimiter"] == ";"
        assert p["rows"] == [{"tracking": "0151086", "amount": 19.99}]

    def test_tab_delimited(self):
        p = cod._parse_payout("Товарителница\tСума\n1234567890\t25.50\n")
        assert p["delimiter"] == "\t"
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_pipe_delimited(self):
        p = cod._parse_payout("awb|amount\n1234567890|25.50\n")
        assert p["delimiter"] == "|"
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_quoted_field_containing_delimiter(self):
        # amount is quoted and holds the delimiter char inside — csv must respect the quotes
        p = cod._parse_payout('awb;amount\n"0151086";"1.234,56"\n')
        assert p["rows"] == [{"tracking": "0151086", "amount": 1234.56}]

    def test_quoted_tracking_with_spaces(self):
        p = cod._parse_payout('Товарителница,Сума\n"BG 123 456",25.50\n')
        assert p["rows"] == [{"tracking": "123456", "amount": 25.5}]

    def test_positional_fallback_no_header(self):
        # no track-like header word -> col0 = tracking, rightmost numeric = amount
        p = cod._parse_payout("111222333,Иван Петров,42.00\n")
        assert p["rows"] == [{"tracking": "111222333", "amount": 42.0}]

    def test_positional_rightmost_numeric_wins(self):
        # two numeric columns, no header: the RIGHTMOST numeric (not col0) is the amount
        p = cod._parse_payout("1234567890,7,42.00\n")
        assert p["rows"][0]["tracking"] == "1234567890"
        assert p["rows"][0]["amount"] == 42.0

    def test_multiple_rows(self):
        p = cod._parse_payout("awb,cod\n111,10.00\n222,20.50\n333,5\n")
        assert [r["tracking"] for r in p["rows"]] == ["111", "222", "333"]
        assert [r["amount"] for r in p["rows"]] == [10.0, 20.5, 5.0]

    # --- degenerate inputs -----------------------------------------------------------
    def test_empty_file(self):
        p = cod._parse_payout("")
        assert p["rows"] == [] and p["skipped"] == 0 and p["columns"] == {}

    def test_whitespace_only_file(self):
        p = cod._parse_payout("\n \n\n")
        assert p["rows"] == []

    def test_header_only(self):
        p = cod._parse_payout("Товарителница,Наложен платеж\n")
        assert p["rows"] == [] and p["skipped"] == 0

    def test_ragged_rows_short(self):
        # a row shorter than the amount column index must be skipped, not crash
        p = cod._parse_payout("Товарителница,Име,Сума\n1234567890,Иван,25.50\n999\n")
        assert {r["tracking"] for r in p["rows"]} == {"1234567890"}
        assert p["skipped"] == 1  # the ragged "999" row has no amount

    def test_ragged_rows_extra_columns(self):
        # extra trailing columns don't break tracking/amount selection
        p = cod._parse_payout("Товарителница,Сума\n1234567890,25.50,extra,junk\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_missing_amount_column_all_skipped(self):
        # only a tracking column exists -> no amount detectable -> every row skipped
        p = cod._parse_payout("Товарителница\n1234567890\n2223334\n")
        assert p["rows"] == [] and p["skipped"] == 2

    def test_row_missing_tracking_skipped(self):
        p = cod._parse_payout("Товарителница,Сума\n,25.50\n1234567890,10.00\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 10.0}]
        assert p["skipped"] == 1

    def test_row_missing_amount_skipped(self):
        p = cod._parse_payout("Товарителница,Сума\n1234567890,\n2223334,10.00\n")
        assert {r["tracking"] for r in p["rows"]} == {"2223334"}
        assert p["skipped"] == 1

    def test_zero_amount_is_kept_not_skipped(self):
        # a genuine 0.00 collection is a valid amount (amount is not None), must be kept
        p = cod._parse_payout("Товарителница,Сума\n1234567890,0.00\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 0.0}]
        assert p["skipped"] == 0

    def test_negative_amount_kept(self):
        # a deduction / negative line is parsed (out-of-range filtering happens later)
        p = cod._parse_payout("Товарителница,Сума\n1234567890,-5.00\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": -5.0}]

    # --- decoy amount column that must NOT be chosen ---------------------------------
    def test_decoy_payer_name_column_not_chosen_as_amount(self):
        # "Платец" matches the amount hint "плат", but it holds a NAME (no numbers),
        # so the _col_has_numbers guard rejects it and the real amount col is used.
        p = cod._parse_payout("Товарителница,Платец,Наложен платеж\n1234567890,Иван Петров,25.50\n")
        assert p["columns"]["amount"] == "наложен платеж"
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_decoy_multiple_hint_columns_prefers_numeric(self):
        # "Сума за клиент" and real "Наложен платеж": only one carries numbers
        p = cod._parse_payout(
            "Товарителница,Получател,Наложен платеж\n1234567890,Мария,33.33\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 33.33}]

    # --- decoy-column resolution (fixed: value-aware column choice) -------------------
    def test_client_number_decoy_not_chosen_as_tracking(self):
        # "Номер на клиент" matches the 'номер' hint but holds a short id; the real waybill column
        # (long digits) must win.
        p = cod._parse_payout("Номер на клиент,Товарителница,Сума\n555,1234567890,25.50\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]

    def test_numeric_payer_decoy_not_chosen_as_amount(self):
        # "Платец" (numeric payer id) matches 'плат' but is an integer; the money column (decimals) wins.
        p = cod._parse_payout("Товарителница,Платец,Сума\n1234567890,88888,25.50\n")
        assert p["rows"] == [{"tracking": "1234567890", "amount": 25.5}]


# =====================================================================================
# quality.ean_valid — EAN-8 / EAN-13 check digit
# =====================================================================================
class TestEanValid:
    @pytest.mark.parametrize("code", [
        "96385074",          # valid EAN-8
        "5901234123457",     # valid EAN-13
        "4006381333931",     # valid EAN-13
        "9780306406157",     # valid EAN-13 (ISBN)
    ])
    def test_valid(self, code):
        assert quality.ean_valid(code) is True

    def test_valid_with_separators_stripped(self):
        # non-digits are stripped before validation
        assert quality.ean_valid("9 6 3 8 5 0 7 4") is True
        assert quality.ean_valid("5901234-123457") is True

    @pytest.mark.parametrize("code", [
        "96385075",          # wrong check digit (EAN-8)
        "5901234123458",     # wrong check digit (EAN-13)
        "12345678",          # 8 digits, bad check
        "1234567890123",     # 13 digits, bad check
    ])
    def test_wrong_check_digit(self, code):
        assert quality.ean_valid(code) is False

    @pytest.mark.parametrize("code", [
        "1234567",           # 7 digits
        "123456789",         # 9 digits
        "123456789012",      # 12 digits (UPC-A-ish, not 8/13)
        "12345678901234",    # 14 digits
        "",                  # empty
        "abcdefgh",          # non-digits -> "" after strip
        None,                # None
    ])
    def test_wrong_length_or_nondigit(self, code):
        assert quality.ean_valid(code) is False


# =====================================================================================
# quality.is_internal_barcode — Darling's own 380013 prefix
# =====================================================================================
class TestIsInternalBarcode:
    @pytest.mark.parametrize("code, expected", [
        ("3800130000001", True),
        ("380013", True),
        ("38-0013-000", True),          # separators stripped first
        ("3800140000000", False),       # 380014 != 380013
        ("5901234123457", False),
        ("", False),
        (None, False),
        ("38001", False),               # shorter than prefix
    ])
    def test_internal(self, code, expected):
        assert quality.is_internal_barcode(code) is expected


# =====================================================================================
# quality.norm_name — near-duplicate normalization key
# =====================================================================================
class TestNormName:
    @pytest.mark.parametrize("raw, expected", [
        ("  Ножица,  Голяма!! ", "ножица голяма"),
        ("ABC   def", "abc def"),
        ("Café-Bar", "café bar"),
        ("Ножица", "ножица"),
        ("!!!", ""),                    # punctuation-only collapses to empty
        ("", ""),
        (None, ""),
        (123, "123"),                   # non-string stringified
        ("A_B", "a_b"),                 # underscore is a word char, kept
        ("Продукт   №5", "продукт 5"),  # № is punctuation -> collapsed
    ])
    def test_norm_name(self, raw, expected):
        assert quality.norm_name(raw) == expected

    def test_case_and_whitespace_fold_to_same_key(self):
        assert quality.norm_name("Ножица  ГОЛЯМА") == quality.norm_name("ножица голяма")
        assert quality.norm_name("ножица, голяма!") == quality.norm_name("Ножица Голяма")


# =====================================================================================
# quality.check_article — per-field completeness messages
# =====================================================================================
def _clean_article():
    return {
        "article_name": "Тестова ножица",
        "barcode": "5901234123457",     # valid EAN-13
        "actual_price": 9.99,
        "master_cat": "Инструменти",
        "description": "Описание с достатъчна дължина.",
        "picture": "http://img/x.jpg",
    }


class TestCheckArticle:
    def test_clean_article_has_no_issues(self):
        assert quality.check_article(_clean_article()) == []

    def test_name_from_alt_key(self):
        a = _clean_article(); del a["article_name"]; a["name"] = "Алт име"
        assert "няма име" not in quality.check_article(a)

    def test_missing_name(self):
        a = _clean_article(); a["article_name"] = "   "  # whitespace only
        assert "няма име" in quality.check_article(a)

    def test_missing_barcode(self):
        a = _clean_article(); a["barcode"] = ""
        assert "няма баркод" in quality.check_article(a)

    def test_internal_barcode_message(self):
        a = _clean_article(); a["barcode"] = "3800130000001"
        issues = quality.check_article(a)
        assert "вътрешен код (не е реален GTIN)" in issues
        assert "невалиден баркод (контролна цифра)" not in issues  # internal wins

    def test_invalid_check_digit_barcode(self):
        a = _clean_article(); a["barcode"] = "5901234123458"
        assert "невалиден баркод (контролна цифра)" in quality.check_article(a)

    def test_barcode_as_list_takes_first(self):
        a = _clean_article(); a["barcode"] = ["", "5901234123457"]
        assert "няма баркод" not in quality.check_article(a)
        assert "невалиден баркод (контролна цифра)" not in quality.check_article(a)

    @pytest.mark.parametrize("price", [None, 0, 0.0, -5, "0"])
    def test_missing_or_nonpositive_price(self, price):
        a = _clean_article(); a["actual_price"] = price; a.pop("price", None)
        assert "няма продажна цена" in quality.check_article(a)

    @pytest.mark.parametrize("price", ["abc", [], {}, "лв."])
    def test_invalid_price_value(self, price):
        a = _clean_article(); a["actual_price"] = price
        assert "невалидна цена" in quality.check_article(a)

    def test_price_fallback_to_price_key(self):
        a = _clean_article(); a["actual_price"] = None; a["price"] = 12.5
        assert "няма продажна цена" not in quality.check_article(a)

    def test_price_string_numeric_ok(self):
        a = _clean_article(); a["actual_price"] = "12.50"
        assert "няма продажна цена" not in quality.check_article(a)
        assert "невалидна цена" not in quality.check_article(a)

    def test_missing_category(self):
        a = _clean_article(); a["master_cat"] = ""
        assert "няма категория" in quality.check_article(a)

    def test_category_alt_keys(self):
        a = _clean_article(); a["master_cat"] = ""; a["category"] = "Дом"
        assert "няма категория" not in quality.check_article(a)

    @pytest.mark.parametrize("desc", ["", "къс", "123456789"])  # <10 chars
    def test_short_description(self, desc):
        a = _clean_article(); a["description"] = desc
        assert "кратко/липсващо описание" in quality.check_article(a)

    def test_description_exactly_10_ok(self):
        a = _clean_article(); a["description"] = "1234567890"  # exactly 10
        assert "кратко/липсващо описание" not in quality.check_article(a)

    def test_missing_picture(self):
        a = _clean_article(); a["picture"] = ""
        assert "няма снимка" in quality.check_article(a)

    def test_picture_alt_key(self):
        a = _clean_article(); a["picture"] = ""; a["image"] = "x.jpg"
        assert "няма снимка" not in quality.check_article(a)

    def test_all_issues_at_once(self):
        issues = quality.check_article({})
        for msg in ("няма име", "няма баркод", "няма продажна цена",
                    "няма категория", "кратко/липсващо описание", "няма снимка"):
            assert msg in issues


# =====================================================================================
# quality.audit_catalog — cross-article duplicate detection
# =====================================================================================
class TestAuditCatalog:
    def test_empty_input(self):
        r = quality.audit_catalog([])
        assert r["total"] == 0 and r["flagged_count"] == 0 and r["clean_count"] == 0
        assert r["duplicate_barcodes"] == 0 and r["flagged"] == []

    def test_none_input(self):
        r = quality.audit_catalog(None)
        assert r["total"] == 0 and r["flagged"] == []

    def test_single_clean_article(self):
        r = quality.audit_catalog([dict(_clean_article(), article_id=1, code="C1")])
        assert r["total"] == 1 and r["flagged_count"] == 0 and r["clean_count"] == 1

    def test_duplicate_barcode(self):
        rows = [
            dict(_clean_article(), article_id=1, code="C1"),
            dict(_clean_article(), article_id=2, code="C2"),  # same valid barcode
        ]
        r = quality.audit_catalog(rows)
        assert r["duplicate_barcodes"] == 1
        for f in r["flagged"]:
            assert "дублиран баркод" in f["issues"]

    def test_internal_barcode_not_counted_as_duplicate(self):
        # 380013 internal codes are excluded from barcode-dup detection
        rows = [
            dict(_clean_article(), article_id=1, code="C1", barcode="3800130000001"),
            dict(_clean_article(), article_id=2, code="C2", barcode="3800130000001"),
        ]
        r = quality.audit_catalog(rows)
        assert r["duplicate_barcodes"] == 0
        for f in r["flagged"]:
            assert "дублиран баркод" not in f["issues"]

    def test_duplicate_code(self):
        rows = [
            dict(_clean_article(), article_id=1, code="SAME"),
            dict(_clean_article(), article_id=2, code="SAME"),
        ]
        r = quality.audit_catalog(rows)
        assert r["duplicate_codes"] == 1
        assert all("дублиран код" in f["issues"] for f in r["flagged"])

    def test_near_duplicate_name(self):
        rows = [
            dict(_clean_article(), article_id=1, code="C1", article_name="Ножица голяма"),
            dict(_clean_article(), article_id=2, code="C2", article_name="ножица  ГОЛЯМА!"),
        ]
        r = quality.audit_catalog(rows)
        assert r["duplicate_names"] == 1
        assert all("възможен дубликат (име)" in f["issues"] for f in r["flagged"])

    def test_distinct_names_not_flagged_as_dup(self):
        rows = [
            dict(_clean_article(), article_id=1, code="C1", article_name="Ножица"),
            dict(_clean_article(), article_id=2, code="C2", article_name="Чук"),
        ]
        r = quality.audit_catalog(rows)
        assert r["duplicate_names"] == 0

    def test_counts_add_up(self):
        rows = [
            # distinct valid barcodes so article 1 stays clean (no cross-article dup)
            dict(_clean_article(), article_id=1, code="C1", barcode="4006381333931",
                 article_name="Чук"),
            dict(_clean_article(), article_id=2, code="C2", barcode="",
                 article_name="Отвертка"),  # flagged: no barcode
        ]
        r = quality.audit_catalog(rows)
        assert r["total"] == 2
        assert r["flagged_count"] + r["clean_count"] == r["total"]
        assert r["flagged_count"] == 1


# =====================================================================================
# nekorekten.normalize_phone — canonical BG (359-prefixed digits)
# =====================================================================================
class TestNormalizePhone:
    @pytest.mark.parametrize("raw, expected", [
        ("0888123456", "359888123456"),        # 0-prefixed national
        ("0888 123 456", "359888123456"),       # with spaces
        ("0888-123-456", "359888123456"),       # with dashes
        ("+359888123456", "359888123456"),       # +359
        ("359888123456", "359888123456"),        # already canonical
        ("00359888123456", "359888123456"),      # 00-prefixed intl
        ("888123456", "359888123456"),           # bare 9-digit national
        ("(0888) 123 456", "359888123456"),      # parens
        ("00888123456", "359888123456"),         # 00 + 9-digit national
    ])
    def test_bg_numbers(self, raw, expected):
        assert nekorekten.normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", "---", None, "()"])
    def test_empty_becomes_empty(self, raw):
        assert nekorekten.normalize_phone(raw) == ""

    def test_non_bg_left_as_digits(self):
        # a foreign number (not 00/359/0-prefixed, not 9 digits) is returned digits-only
        assert nekorekten.normalize_phone("+1 202 555 0173") == "12025550173"
        assert nekorekten.normalize_phone("0044 20 7946 0000") == "442079460000"

    def test_idempotent_on_canonical(self):
        once = nekorekten.normalize_phone("0888123456")
        assert nekorekten.normalize_phone(once) == once

    def test_same_number_across_formats_matches(self):
        forms = ["0888123456", "+359888123456", "00359888123456", "888123456",
                 "0888 123 456", "0888-123-456"]
        canon = {nekorekten.normalize_phone(f) for f in forms}
        assert canon == {"359888123456"}

    def test_degenerate_single_zero(self):
        # documented edge: "0" -> startswith("0") -> "359" + "" == "359"
        assert nekorekten.normalize_phone("0") == "359"

    def test_double_prefixed_not_re_cleaned(self):
        # malformed 00359 + 0-national is NOT re-normalized (GIGO, documented behavior):
        # digits "003590888123456" -> strip "00" -> "3590888123456" -> starts 359 -> as-is
        assert nekorekten.normalize_phone("00359 0888 123 456") == "3590888123456"


# =====================================================================================
# nekorekten._report_count — tolerant response-shape counting
# =====================================================================================
class TestReportCount:
    @pytest.mark.parametrize("data, expected", [
        ({"items": [1, 2, 3], "count": 50}, 50),   # count is total-across-pages, wins
        ({"count": 7}, 7),
        ({"count": 0}, 0),                          # genuine zero
        ({"items": [1, 2]}, 2),                     # no count -> len(items)
        ({"data": [1, 2, 3]}, 3),                   # {data:[...]}
        ({"items": []}, 0),
        ([1, 2, 3, 4], 4),                          # bare list
        ([], 0),
        ({"total": 12}, 12),                        # top-level total
        ({"total": "12"}, 12),                      # string total via int()
        ({"meta": {"total": 9}}, 9),                # meta.total
        ({"meta": {"total": "5"}}, 5),
        ({}, 0),
        (None, 0),
        ("garbage", 0),
        (42, 0),
        ({"total": "abc"}, 0),                      # unparseable total -> 0
        ({"count": "50"}, 0),                       # count must be int; string ignored -> 0
        ({"count": 3.5}, 0),                        # float count ignored (not int)
        ({"items": "not-a-list"}, 0),               # items must be a list
    ])
    def test_report_count(self, data, expected):
        assert nekorekten._report_count(data) == expected

    def test_meta_total_preferred_over_toplevel_total(self):
        assert nekorekten._report_count({"meta": {"total": 3}, "total": 99}) == 3

    def test_bool_count_is_int_subclass(self):
        # bool is a subclass of int; True passes the isinstance check (== 1). Documented quirk.
        assert nekorekten._report_count({"count": True}) == 1


# =====================================================================================
# pricing._num / _cost / _resolve_markup / _propose
# =====================================================================================
class TestPricingNum:
    @pytest.mark.parametrize("v, expected", [
        (None, None),
        ("12.5", 12.5),
        (12.5, 12.5),
        (0, 0.0),                    # zero is not None -> 0.0 (NOT None)
        ("0", 0.0),
        (12.34567, 12.3457),         # rounds to 4 places
        (True, 1.0),                 # bool -> float
        ("abc", None),
        ("", None),
        ("1,5", None),               # comma-decimal NOT handled here (unlike orders._to_kg)
        ([], None),
        ({}, None),
    ])
    def test_num(self, v, expected):
        assert pricing._num(v) == expected


class TestPricingCost:
    def test_delivery_price_wins(self):
        assert pricing._cost({"delivery_price": 2.5, "avg_delivery_price": 9.0}) == 2.5

    def test_zero_delivery_price_falls_back_to_avg(self):
        # _num(0) == 0.0 which is falsy, so `or` falls through to avg
        assert pricing._cost({"delivery_price": 0, "avg_delivery_price": 5.0}) == 5.0

    def test_invalid_delivery_price_falls_back(self):
        assert pricing._cost({"delivery_price": "n/a", "avg_delivery_price": 3.5}) == 3.5

    def test_both_missing(self):
        assert pricing._cost({}) is None

    def test_both_zero_returns_zero(self):
        # 0 or 0.0 -> 0.0 (falsy but returned by `or` as the last operand)
        assert pricing._cost({"delivery_price": 0, "avg_delivery_price": 0}) == 0.0

    def test_avg_only(self):
        assert pricing._cost({"avg_delivery_price": 4.2}) == 4.2


class _Rule:
    def __init__(self, markup_pct):
        self.markup_pct = markup_pct


class TestResolveMarkup:
    def test_article_rule_wins(self):
        assert pricing._resolve_markup(_Rule(0.3), "ACME", {"ACME": 0.5}) == (0.3, "product")

    def test_article_rule_zero_still_wins(self):
        # markup 0 is a real value (not None) -> product source, not supplier fallback
        assert pricing._resolve_markup(_Rule(0), "ACME", {"ACME": 0.5}) == (0, "product")

    def test_supplier_fallback(self):
        assert pricing._resolve_markup(None, "ACME", {"ACME": 0.5}) == (0.5, "supplier")

    def test_no_rule_no_supplier_match(self):
        assert pricing._resolve_markup(None, "ACME", {"OTHER": 0.5}) == (None, None)

    def test_empty_supplier_never_matches(self):
        # supplier="" is falsy -> supplier branch skipped even if "" is a key
        assert pricing._resolve_markup(None, "", {"": 0.5}) == (None, None)

    def test_none_everything(self):
        assert pricing._resolve_markup(None, "", {}) == (None, None)


class TestPropose:
    @pytest.mark.parametrize("cost, markup, expected", [
        (1.0, 0.5, 1.5),            # +50%
        (1.0, 0.0, 1.0),            # no markup
        (10.0, 0.005, 10.05),
        (2.0, 0.5, 3.0),
        (0.1, 2.0, 0.3),            # float-noise value quantizes cleanly
        (100.0, 0.2, 120.0),
    ])
    def test_basic(self, cost, markup, expected):
        assert pricing._propose(cost, markup) == expected

    @pytest.mark.parametrize("cost, markup, expected", [
        # Decimal ROUND_HALF_UP (not banker's rounding): the .xx5 boundary rounds UP
        (1.0, 0.505, 1.51),          # 1.505 -> 1.51 (banker's would give 1.50)
        (1.005, 0.0, 1.01),          # 1.005 -> 1.01
        (2.675, 0.0, 2.68),          # classic float case -> 2.68 (banker's/round() gives 2.67)
    ])
    def test_round_half_up_not_bankers(self, cost, markup, expected):
        assert pricing._propose(cost, markup) == expected

    def test_returns_float(self):
        assert isinstance(pricing._propose(1.0, 0.5), float)


# =====================================================================================
# orders._to_kg — weight coercion ("12,5" -> 12.5)
# =====================================================================================
class TestToKg:
    @pytest.mark.parametrize("v, expected", [
        ("12.5", 12.5),
        ("12,5", 12.5),             # comma-decimal handled via replace
        ("0", 0.0),
        ("0.0", 0.0),
        (12.5, 12.5),               # already numeric
        (1, 1.0),
        ("  3.2  ", 3.2),           # whitespace tolerated by float()
        ("-5", -5.0),               # negative passes through (pure fn)
    ])
    def test_valid(self, v, expected):
        assert orders._to_kg(v) == expected

    @pytest.mark.parametrize("v", ["", None, 0, 0.0, False, [], {}])
    def test_falsy_returns_zero(self, v):
        # `if v` short-circuits falsy inputs to 0.0 before any float() attempt
        assert orders._to_kg(v) == 0.0

    @pytest.mark.parametrize("v", ["abc", "1.234,56", "1,2,3", "kg", [1], "12.5.6"])
    def test_unparseable_returns_zero(self, v):
        # note: unlike cod._parse_amount, thousands like "1.234,56" are NOT handled here
        assert orders._to_kg(v) == 0.0
