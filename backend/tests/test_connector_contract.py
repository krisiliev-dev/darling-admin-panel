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

    # account identity is idempotent by the Shopify order id
    assert body["account"]["uuid"] == "shopify-12345"
    assert body["account"]["contact_name"] == "Иван Петров"
    assert body["account"]["phone"] == "0888111222"

    # each line maps SKU→article_id, quantity→amount(str), price→current_price(str)
    assert body["orders"] == [
        {"article_id": 501, "amount": "2", "current_price": "9.90"},
        {"article_id": 502, "amount": "1", "current_price": "4.50"},
    ]

    # a single payment for the FULL order total is recorded as collected online
    assert len(body["payments"]) == 1
    pay = body["payments"][0]
    assert pay["paymethod_id"] == 7
    assert pay["actual_paid_sum"] == "24.30"       # == order total_price
    assert pay["transaction_id"] == "12345"


def test_unmatched_skus_are_surfaced_not_dropped(conn):
    order = _order(line_items=[
        {"sku": "SKU-1", "quantity": 1, "price": "9.90"},
        {"sku": "GHOST", "quantity": 3, "price": "1.00"},
    ])
    res = connector.shopify_order_to_barsy(order, mode="sale", do_write=True)
    assert res["missing_skus"] == ["GHOST"]
    body = conn.body_for("Accounts_place")
    assert [r["article_id"] for r in body["orders"]] == [501]   # ghost line excluded


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
    assert body["client_order"]["uuid"] == "shopify-12345"
    assert len(body["rows"]) == 2


# ---------------------------------------------------------- refund / cancellation (storno) ---
def test_refund_places_partial_storno_referencing_the_original_sale(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    refund = {"id": 55, "order_id": 12345, "refund_line_items": [
        {"quantity": 1, "line_item": {"sku": "SKU-1"}},
    ]}
    res = connector.shopify_refund_to_barsy(refund, do_write=True)
    assert res.get("written") is True
    body = conn.body_for("Accounts_place")
    assert body["account"]["storno_of_account_id"] == 900       # reverses the ORIGINAL sale
    assert body["account"]["uuid"] == "shopify-refund-55"
    # only the refunded line + quantity is restocked; storno carries no payment
    assert body["orders"] == [{"article_id": 501, "amount": "1"}]
    assert body["payments"] == []


def test_refund_without_a_logged_sale_is_skipped(conn):
    res = connector.shopify_refund_to_barsy({"id": 5, "order_id": 999, "refund_line_items": []}, do_write=True)
    assert "skipped" in res
    assert conn.calls == []


def test_cancellation_places_full_storno(conn, monkeypatch):
    monkeypatch.setattr(connector, "_seen_order", lambda oid: {"order_id": oid, "account_id": 900})
    res = connector.shopify_order_cancellation_to_barsy(_order(cancel_reason="customer"), do_write=True)
    assert res.get("written") is True
    body = conn.body_for("Accounts_place")
    assert body["account"]["storno_of_account_id"] == 900
    assert body["account"]["uuid"] == "shopify-storno-12345"
    assert body["orders"] == [] and body["payments"] == []


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
