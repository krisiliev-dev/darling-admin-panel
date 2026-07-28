# -*- coding: utf-8 -*-
"""Contract tests for the Shopify→Barsy money path (the connector).

Unlike test_webhooks.py — which mocks the connector away and only checks receiver
gating/dispatch — these tests exercise the REAL connector translation functions and assert the
EXACT Barsy request bodies they build. This is the single most financially consequential path in
the system (sale / payment / refund / storno / customer), so the body shapes are pinned here:
a wrong SKU→article, quantity, price, payment sum, or storno reference would otherwise pass the
whole suite silently.

The connector's Barsy client (`connector.b`) and its side-effecting helpers (`_catalog`,
`_seen_order`, `_record_order`, `_paymethod_id`) are stubbed so nothing touches the real ERP or
writes state files; every `b.call(op, body)` is recorded for assertion.
"""
import pytest

from app.core import connector


class _Rec:
    """Records connector.b.call(op, body) and returns a per-op configured value."""
    def __init__(self):
        self.calls = []
        self.returns = {}

    def call(self, operation, body=None, *a, **k):
        self.calls.append((operation, body))
        return self.returns.get(operation, 111)

    def body_for(self, operation):
        for op, body in self.calls:
            if op == operation:
                return body
        raise KeyError(f"no {operation!r} call; saw {[o for o, _ in self.calls]}")


@pytest.fixture
def conn(monkeypatch):
    """Stub the connector's ERP client + side-effecting helpers; return the call recorder.

    Catalog maps two SKUs to article ids so line-item mapping (and a missing SKU) is testable.
    """
    rec = _Rec()
    monkeypatch.setattr(connector, "b", rec)
    monkeypatch.setattr(connector, "_catalog", lambda: {
        "SKU-1": {"article_id": 501, "actual_price": "9.90"},
        "SKU-2": {"article_id": 502, "actual_price": "4.50"},
    })
    monkeypatch.setattr(connector, "_seen_order", lambda oid: None)     # fresh by default
    monkeypatch.setattr(connector, "_record_order", lambda *a, **k: None)  # no state file
    monkeypatch.setattr(connector, "_paymethod_id", lambda: 7)
    return rec


# A storno reads the sale account's FISCAL-receipt fields (`_account_storno_data`) and REFUSES with
# needs_review:no_unp when they're absent — the real device-less-POS behaviour. So a test that wants to
# reach the storno must hand back a fiscalised account; `unp` is DDDDDDDD-UUUU-NNNNNNN, `fm_num` 8 digits.
_FISCAL_ACCOUNT = {"account": {"unp": "12345678-0001-0000042", "receipt_num": "42",
                               "receipt_date": "2026-07-28", "fm_num": "12345678"}}
_FISCAL_STORNO = {"unp": "12345678-0001-0000042", "receipt_num": "42",
                  "receipt_date": "2026-07-28", "fm_num": "12345678"}


def _fiscalise(rec):
    """Make the account behind a storno fiscally closed, so Accounts_storno is actually emitted."""
    rec.returns["Accounts_get"] = _FISCAL_ACCOUNT
    return rec


def _order(**over):
    o = {
        "id": 12345, "name": "#1001", "total_price": "24.30",
        "financial_status": "paid", "payment_gateway_names": ["shopify_payments"],
        "customer": {"first_name": "Иван", "last_name": "Петров", "phone": "0888111222"},
        "shipping_address": {"name": "Иван Петров", "address1": "ул. Роза 1", "city": "София"},
        "line_items": [
            {"sku": "SKU-1", "quantity": 2, "price": "9.90"},
            {"sku": "SKU-2", "quantity": 1, "price": "4.50"},
        ],
    }
    o.update(over)
    return o


# ---------------------------------------------------------------- sale (Accounts_place) ---
def test_prepaid_sale_places_account_with_lines_and_online_payment(conn):
    res = connector.shopify_order_to_barsy(_order(), mode="sale", do_write=True)
    assert res.get("written") is True
    body = conn.body_for("Accounts_place")

    # account identity is idempotent by the Shopify order id (uuid5 — Barsy rejects raw "shopify-<id>")
    assert body["account"]["uuid"] == connector._acc_uuid("shopify-12345")
    assert body["account"]["contact_name"] == "Иван Петров"
    assert body["account"]["phone"] == "0888111222"

    # each line maps SKU→article_id, quantity→amount(str), price→original_current_price(str).
    # NB `original_current_price` NOT `current_price` (the latter is DEPRECATED → silently dropped →
    # would mis-book the sale at the article's base price).
    assert body["orders"] == [
        {"article_id": 501, "amount": "2", "original_current_price": "9.90"},
        {"article_id": 502, "amount": "1", "original_current_price": "4.50"},
    ]

    # a single payment for the FULL order total is recorded as collected online
    assert len(body["payments"]) == 1
    pay = body["payments"][0]
    assert pay["paymethod_id"] == 7
    assert pay["actual_paid_sum"] == "24.30"       # == order total_price
    assert pay["transaction_id"] == "12345"


def test_unmatched_skus_refuse_the_whole_sale(conn):
    # a partial sale would drift stock vs the Shopify order → refuse and flag for review, write nothing
    order = _order(line_items=[
        {"sku": "SKU-1", "quantity": 1, "price": "9.90"},
        {"sku": "GHOST", "quantity": 3, "price": "1.00"},
    ])
    res = connector.shopify_order_to_barsy(order, mode="sale", do_write=True)
    assert res["missing_skus"] == ["GHOST"] and res.get("needs_review") is True and "error" in res
    with pytest.raises(KeyError):
        conn.body_for("Accounts_place")            # nothing booked


def test_online_payment_is_recorded_non_fiscal(conn):
    # fiscal:0 → book the collected money WITHOUT firing the device (бон printed at packing)
    connector.shopify_order_to_barsy(_order(), mode="sale", do_write=True)
    assert conn.body_for("Accounts_place")["payments"][0]["fiscal"] == 0


def test_cod_sale_records_no_payment(conn):
    res = connector.shopify_order_to_barsy(_order(financial_status="pending"), mode="sale", do_write=True)
    assert res["paid_online"] is False
    assert conn.body_for("Accounts_place")["payments"] == []


def test_no_matching_line_items_writes_nothing(conn):
    order = _order(line_items=[{"sku": "GHOST", "quantity": 1, "price": "1.00"}])
    res = connector.shopify_order_to_barsy(order, mode="sale", do_write=True)
    assert "error" in res
    assert conn.calls == []                       # never calls Barsy with an empty sale


def test_dry_run_never_calls_barsy(conn):
    res = connector.shopify_order_to_barsy(_order(), mode="sale", do_write=False)
    assert res.get("dry_run") is True
    assert conn.calls == []


def test_already_seen_order_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    res = connector.shopify_order_to_barsy(_order(), mode="sale", do_write=True)
    assert "skipped" in res
    assert conn.calls == []                       # no double-sale


def test_clientorder_mode_creates_clientorder_without_payment(conn):
    res = connector.shopify_order_to_barsy(_order(), mode="clientorder", do_write=True)
    assert res.get("clientorder_id") is not None
    assert "Clientorders_create" in [op for op, _ in conn.calls]
    assert "Accounts_place" not in [op for op, _ in conn.calls]   # no fiscal sale/payment
    body = conn.body_for("Clientorders_create")
    # Clientorders_create param key is `order` (ClientOrderInputData), not `client_order`
    assert body["order"]["uuid"] == connector._acc_uuid("shopify-12345")
    assert len(body["rows"]) == 2


# ---------------------------------------------------------- refund / cancellation (storno) ---
# ⚠ SHAPE contract only. Storno uses the dedicated Accounts_storno method (AccountInputData has NO
# storno_of_account_id — the old Accounts_place{storno_of_account_id} silently no-op'd). The EMITTED
# body is pinned here; the live BEHAVIOUR is NOT verified — storno is fiscal + irreversible, so a live
# round-trip is deferred to a fiscal POS + explicit owner go (Hermes). A green test = "we emit the
# right shape", NOT "Barsy accepted it".
def test_refund_stornos_the_refunded_articles(conn, monkeypatch):
    _fiscalise(conn)
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    refund = {"id": 55, "order_id": 12345, "refund_line_items": [
        {"quantity": 1, "line_item": {"sku": "SKU-1"}},
    ]}
    res = connector.shopify_refund_to_barsy(refund, do_write=True)
    assert res.get("written") is True
    # storno the refunded ARTICLE (by article_ids — Barsy can't storno by quantity), full refund
    assert conn.body_for("Accounts_storno") == {
        "account_id": 900, "article_ids": [501], "payments": [],
        "storno_data": {"storno_reason": "return", **_FISCAL_STORNO}}


def test_refund_with_no_matched_articles_refuses_instead_of_whole_account_storno(conn, monkeypatch):
    """article_ids=[] means WHOLE ACCOUNT to Barsy, so an unmapped SKU (or a shipping-only / goodwill
    refund) must NOT reach Accounts_storno — that would irreversibly reverse the entire fiscal sale and
    restock untouched items."""
    _fiscalise(conn)
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    refund = {"id": 56, "order_id": 12345, "refund_line_items": [
        {"quantity": 1, "line_item": {"sku": "SKU-НЯМА-В-BARSY"}},
    ]}
    res = connector.shopify_refund_to_barsy(refund, do_write=True)
    assert res["status"] == "needs_review" and res["reason"] == "no_matched_articles"
    with pytest.raises(KeyError):
        conn.body_for("Accounts_storno")            # nothing was reversed


def test_storno_without_a_fiscal_receipt_is_refused(conn, monkeypatch):
    """Device-less POS: the sale has no УНП, so there is no fiscal document to reverse — the connector
    must surface needs_review:no_unp rather than emit an Accounts_storno that Barsy would reject."""
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    res = connector.shopify_order_cancellation_to_barsy(_order(cancel_reason="customer"), do_write=True)
    assert res["status"] == "needs_review" and res["reason"] == "no_unp"
    with pytest.raises(KeyError):
        conn.body_for("Accounts_storno")


def test_refund_without_a_logged_sale_is_skipped(conn):
    res = connector.shopify_refund_to_barsy({"id": 5, "order_id": 999, "refund_line_items": []}, do_write=True)
    assert "skipped" in res
    assert conn.calls == []


def test_cancellation_stornos_the_whole_account(conn, monkeypatch):
    _fiscalise(conn)
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    res = connector.shopify_order_cancellation_to_barsy(_order(cancel_reason="customer"), do_write=True)
    assert res.get("written") is True
    # empty article_ids = storno the WHOLE account; reason "mistake" (order/operator correction)
    assert conn.body_for("Accounts_storno") == {
        "account_id": 900, "article_ids": [], "payments": [],
        "storno_data": {"storno_reason": "mistake", **_FISCAL_STORNO}}


def test_cancellation_without_logged_sale_is_skipped(conn):
    res = connector.shopify_order_cancellation_to_barsy(_order(), do_write=True)
    assert "skipped" in res
    assert conn.calls == []


# ------------------------------------------------------------------- customer (Clients) ---
def test_customer_upsert_uses_createsmart_with_export_id(conn):
    cust = {"id": 77, "first_name": "Мария", "last_name": "Иванова",
            "phone": "0899000111", "email": "m@x.bg"}
    res = connector.shopify_customer_to_barsy(cust, do_write=True)
    assert res.get("client_id") is not None
    body = conn.body_for("Clients_createsmart")
    assert body["client"] == {
        "client_name": "Мария Иванова", "phone": "0899000111",
        "email": "m@x.bg", "export_id": "shopify-77",
    }


# -------------------------------------------- COD is NOT booked as an online payment (fixed) ---
def test_cod_pending_order_records_no_online_payment(conn):
    """A COD order (financial_status='pending') places the sale (draws stock) but books NO online
    payment — the cash is collected on delivery and reconciled via the panel's COD flow. Booking
    it here would double-count COD in Barsy's ledger (2026-07-26 review fix)."""
    cod = _order(financial_status="pending",
                 payment_gateway_names=["Cash on Delivery (COD)"])
    res = connector.shopify_order_to_barsy(cod, mode="sale", do_write=True)
    body = conn.body_for("Accounts_place")
    assert body["payments"] == []                 # no online payment for COD
    assert body["orders"]                          # but the sale/lines are still placed (stock draws)
    assert res.get("paid_online") is False


def test_paid_order_still_books_the_online_payment(conn):
    res = connector.shopify_order_to_barsy(_order(financial_status="paid"), mode="sale", do_write=True)
    body = conn.body_for("Accounts_place")
    assert len(body["payments"]) == 1 and body["payments"][0]["actual_paid_sum"] == "24.30"
    assert res.get("paid_online") is True


# ---------------------------------------------------------------- COD lifecycle (panel-driven) ---
def test_cod_payment_posts_non_fiscal_against_the_sale(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"account_id": 900})
    monkeypatch.setattr(connector, "_seen_key", lambda key: False)
    res = connector.cod_payment_to_barsy("12345", 19.90, waybill="ECONT-77", do_write=True)
    assert res["written"] is True
    body = conn.body_for("Accounts_place")
    assert body["account_id"] == 900 and body["orders"] == []
    pay = body["payments"][0]
    assert pay["paymethod_id"] == 5 and pay["actual_paid_sum"] == "19.9" and pay["fiscal"] == 0


def test_cod_payment_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"account_id": 900})
    monkeypatch.setattr(connector, "_seen_key", lambda key: True)   # already posted
    res = connector.cod_payment_to_barsy("12345", 19.90, do_write=True)
    assert "skipped" in res
    with pytest.raises(KeyError):
        conn.body_for("Accounts_place")            # no double-post


def test_cod_refusal_stornos_the_sale(conn, monkeypatch):
    _fiscalise(conn)
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": "12345", "account_id": 900})
    monkeypatch.setattr(connector, "_seen_key", lambda key: False)
    res = connector.cod_refused_to_barsy("12345", do_write=True)
    assert res["written"] is True
    # an uncollected/returned COD is a RETURN, not an operator "mistake"
    assert conn.body_for("Accounts_storno")["storno_data"]["storno_reason"] == "return"
    body = conn.body_for("Accounts_storno")                     # RTO → whole-account storno restores stock
    assert body["account_id"] == 900 and body["article_ids"] == []


def test_cod_payment_skips_when_no_logged_sale(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: None)
    res = connector.cod_payment_to_barsy("999", 10.0, do_write=True)
    assert "skipped" in res


# ---------------------------------------------------------------- VAT invoice (фактура) ---
def _order_with_invoice(**over):
    o = _order(**over)
    o["email"] = "buyer@firma.bg"
    o["note_attributes"] = [
        {"name": "darling_invoice", "value": "true"},
        {"name": "darling_invoice_company", "value": "Фирма ЕООД"},
        {"name": "darling_invoice_eik", "value": "123456789"},
        {"name": "darling_invoice_vat", "value": "BG123456789"},
        {"name": "darling_invoice_mol", "value": "Иван Иванов"},
        {"name": "darling_invoice_city", "value": "София"},
        {"name": "darling_invoice_address", "value": "ул. Роза 1, 1000"},
    ]
    return o


def test_company_invoice_builds_invoice_data_and_emails(conn):
    connector.shopify_order_to_barsy(_order_with_invoice(), mode="sale", do_write=True)
    body = conn.body_for("Accounts_place")
    inv = body["invoice_data"]
    assert inv["gen_mode"] == 1
    assert inv["receiver_company_name"] == "Фирма ЕООД"
    assert inv["receiver_identity_num"] == "123456789"     # ЕИК
    assert inv["receiver_vat_num"] == "BG123456789"        # ДДС №
    assert inv["receiver_mol"] == "Иван Иванов"
    assert inv["receiver_town"] == "София" and inv["receiver_address"] == "ул. Роза 1, 1000"
    # so Barsy can email the issued фактура
    assert body["account"]["email_invoices"] == "buyer@firma.bg"


def test_no_invoice_when_not_requested(conn):
    connector.shopify_order_to_barsy(_order(), mode="sale", do_write=True)
    assert "invoice_data" not in conn.body_for("Accounts_place")


# ---------------------------------------------------------------- backorder metafield ---
def _stock_conn(conn, monkeypatch, sbz):
    monkeypatch.setattr(connector, "_catalog", lambda: {"C1": {"article_id": 1, "sale_below_zero": sbz}})
    monkeypatch.setattr(connector, "_find_shopify", lambda code: ("gid://Product/1", "gid://Variant/1"))
    monkeypatch.setattr(connector, "_shopify_location", lambda: "gid://Location/1")
    monkeypatch.setattr(connector.s, "gql", lambda q, v=None: {
        "productVariant": {"inventoryItem": {"id": "gid://Inv/1"}}})
    calls = []
    monkeypatch.setattr(connector.s, "gql", lambda q, v=None: (
        calls.append((q, v)) or {"productVariant": {"inventoryItem": {"id": "gid://Inv/1"}}}))
    return calls


def test_backorder_metafield_true_for_oversellable(conn, monkeypatch):
    calls = _stock_conn(conn, monkeypatch, "YES")
    connector.barsy_quantity_change_to_shopify("C1", qty=0, do_write=True)
    mf = next(v["m"][0] for q, v in calls if "metafieldsSet" in q)
    assert mf["namespace"] == "darling" and mf["key"] == "backorder" and mf["value"] == "true"


def test_backorder_metafield_false_for_normal_product(conn, monkeypatch):
    calls = _stock_conn(conn, monkeypatch, "NO")
    connector.barsy_quantity_change_to_shopify("C1", qty=0, do_write=True)
    mf = next(v["m"][0] for q, v in calls if "metafieldsSet" in q)
    assert mf["value"] == "false"
