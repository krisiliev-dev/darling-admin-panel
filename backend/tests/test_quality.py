# -*- coding: utf-8 -*-
"""Product-data quality: EAN check-digit, internal-prefix flag, per-article completeness, whole-
catalog duplicate detection, and the /audit + /check endpoints."""

from app import quality


def test_ean_check_digit():
    assert quality.ean_valid("5901234123457") is True      # valid EAN-13
    assert quality.ean_valid("5901234123456") is False     # wrong check digit
    assert quality.ean_valid("40170725") is True           # valid EAN-8
    assert quality.ean_valid("123") is False               # wrong length


def test_internal_prefix_flag():
    assert quality.is_internal_barcode("3800131234567") is True   # Darling internal 380013…
    assert quality.is_internal_barcode("5901234123457") is False


def test_check_article_flags_gaps():
    clean = {"article_name": "Чаша Bormioli 380 мл", "barcode": "5901234123457",
             "actual_price": 4.5, "master_cat": "Чаши", "description": "Стъклена чаша 380 мл, прозрачна.",
             "picture": "http://x/y.jpg"}
    assert quality.check_article(clean) == []

    bad = {"article_name": "", "barcode": "3800131234567", "actual_price": 0,
           "description": "", "picture": ""}
    issues = quality.check_article(bad)
    assert "няма име" in issues
    assert "вътрешен код (не е реален GTIN)" in issues
    assert "няма продажна цена" in issues
    assert "няма категория" in issues
    assert any("описание" in i for i in issues)
    assert "няма снимка" in issues


def test_audit_detects_duplicates():
    rows = [
        {"article_id": 1, "code": "A", "article_name": "Чаша синя", "barcode": "5901234123457", "actual_price": 2, "master_cat": "Ч", "description": "описание тук е", "picture": "p"},
        {"article_id": 2, "code": "A", "article_name": "Чаша  Синя", "barcode": "5901234123457", "actual_price": 2, "master_cat": "Ч", "description": "описание тук е", "picture": "p"},
    ]
    rep = quality.audit_catalog(rows)
    assert rep["duplicate_barcodes"] == 1 and rep["duplicate_codes"] == 1 and rep["duplicate_names"] == 1
    assert rep["flagged_count"] == 2
    joined = [i for f in rep["flagged"] for i in f["issues"]]
    assert "дублиран баркод" in joined and "възможен дубликат (име)" in joined


# --- endpoints -----------------------------------------------------------------------
async def test_check_endpoint(owner_client):
    r = await owner_client.post("/api/quality/check", json={"name": "Тест", "barcode": "5901234123457",
                                "price": 3.0, "category": "Дом", "description": "Достатъчно описание тук.", "picture": "p"})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = await owner_client.post("/api/quality/check", json={"name": "", "barcode": "123", "price": 0})
    body = r.json()
    assert body["ok"] is False and "няма име" in body["issues"]


async def test_audit_endpoint(owner_client, barsy_recorder):
    barsy_recorder.set_return("Articles_getlist", [
        {"article_id": 1, "code": "A", "article_name": "Добър продукт", "barcode": "5901234123457",
         "actual_price": 5, "master_cat": "Дом", "description": "Пълно описание на продукта.", "picture": "p"},
        {"article_id": 2, "code": "B", "article_name": "Липсва баркод", "actual_price": 3,
         "master_cat": "Дом", "description": "Описание достатъчно.", "picture": "p"},
    ])
    r = await owner_client.get("/api/quality/audit")
    assert r.status_code == 200
    rep = r.json()
    assert rep["total"] == 2 and rep["flagged_count"] == 1
    assert rep["flagged"][0]["article_id"] == 2 and "няма баркод" in rep["flagged"][0]["issues"]
