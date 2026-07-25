# -*- coding: utf-8 -*-
"""Courier payout-file import: tolerant parsing + tracking-match preview (writes nothing)."""
import pytest

from app.routers import cod

# --- parser unit tests --------------------------------------------------------------
def test_parse_comma_with_bg_headers():
    text = "Товарителница,Наложен платеж\n1234567890,25.50\n9999,10\n"
    p = cod._parse_payout(text)
    assert p["delimiter"] == ","
    assert {r["tracking"] for r in p["rows"]} == {"1234567890", "9999"}
    assert p["rows"][0]["amount"] == 25.5


def test_parse_semicolon_comma_decimal():
    text = "awb;cod\n0151086;19,99\n"
    p = cod._parse_payout(text)
    assert p["delimiter"] == ";"
    assert p["rows"][0] == {"tracking": "0151086", "amount": 19.99}


def test_parse_positional_fallback_no_header():
    # no recognisable header → col0 = tracking, rightmost numeric = amount
    text = "111222333,Иван Петров,42.00\n"
    p = cod._parse_payout(text)
    assert p["rows"][0]["tracking"] == "111222333"
    assert p["rows"][0]["amount"] == 42.0


def test_norm_tracking_strips_non_digits():
    assert cod._norm_tracking(" BG 123-456 ") == "123456"


def test_parse_amount_thousands_and_currency():
    assert cod._parse_amount("1.234,56 лв.") == 1234.56
    assert cod._parse_amount("—") is None


def test_decode_cp1251():
    raw = "Товарителница,Сума\n123,5".encode("cp1251")
    assert "Товарителница" in cod._decode(raw)


# --- endpoint: match against awaiting orders ----------------------------------------
@pytest.fixture
def fake_state(monkeypatch):
    """Two COD orders awaiting settlement, one in transit."""
    state = {
        "gid1": {"order_name": "#1001", "status": "delivered", "courier": "speedy",
                 "tracking": "1234567890", "cod_amount": 25.50},
        "gid2": {"order_name": "#1002", "status": "delivered", "courier": "econt",
                 "tracking": "5550001", "cod_amount": 40.00},
    }
    monkeypatch.setattr(cod.shopify_orders, "state_map", lambda: state)
    return state


async def test_import_payout_matches_and_flags(owner_client, fake_state):
    # file settles #1001 exactly, reports an unknown tracking, and omits #1002 (delivered-not-paid)
    csv_bytes = "Товарителница,Наложен платеж\n1234567890,25.50\n8887776,12.00\n".encode("utf-8")
    r = await owner_client.post("/api/cod/import-payout",
                                files={"file": ("speedy_payout.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["matched"]) == 1
    m = body["matched"][0]
    assert m["order_id"] == "gid1" and m["collected"] == 25.5 and m["discrepancy"] is False
    assert body["unmatched_rows"] == [{"tracking": "8887776", "collected": 12.0}]
    assert [o["order_id"] for o in body["delivered_not_paid"]] == ["gid2"]
    assert body["settle_lines"] == [{"order_id": "gid1", "collected": 25.5}]


# --- endpoint: courier auto-pull (same reconcile shape, sourced from the courier API) ----------
async def test_reconcile_auto_pulls_and_matches(owner_client, fake_state, monkeypatch):
    class FakeAdapter:
        def configured(self):
            return True
        def payout_report(self, df, dt):   # what the Speedy/Econt adapter returns at launch
            return [{"waybill": "1234567890", "collected": 25.50, "fee": 1.2, "payout_date": "2026-07-25"},
                    {"waybill": "8887776", "collected": 12.0}]
    monkeypatch.setattr(cod.courier_mod, "get", lambda name: FakeAdapter())
    r = await owner_client.post("/api/cod/reconcile-auto",
                                json={"courier": "speedy", "date_from": "2026-07-01", "date_to": "2026-07-25"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_pulled"] == 2 and body["courier"] == "speedy"
    assert len(body["matched"]) == 1 and body["matched"][0]["order_id"] == "gid1"
    assert body["matched"][0]["fee"] == 1.2 and body["matched"][0]["payout_date"] == "2026-07-25"
    assert body["unmatched_rows"] == [{"tracking": "8887776", "collected": 12.0}]
    assert [o["order_id"] for o in body["delivered_not_paid"]] == ["gid2"]
    assert body["settle_lines"] == [{"order_id": "gid1", "collected": 25.5}]


async def test_reconcile_auto_rejects_unknown_courier(owner_client, fake_state):
    r = await owner_client.post("/api/cod/reconcile-auto",
                                json={"courier": "dhl", "date_from": "2026-07-01", "date_to": "2026-07-25"})
    assert r.status_code == 400


async def test_reconcile_auto_requires_staff(client):
    r = await client.post("/api/cod/reconcile-auto",
                          json={"courier": "speedy", "date_from": "2026-07-01", "date_to": "2026-07-25"})
    assert r.status_code == 401


async def test_import_payout_flags_discrepancy(owner_client, fake_state):
    csv_bytes = "awb;cod\n1234567890;20.00\n".encode("utf-8")  # 20 collected vs 25.50 expected
    r = await owner_client.post("/api/cod/import-payout",
                                files={"file": ("p.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200
    assert r.json()["matched"][0]["discrepancy"] is True


async def test_import_payout_rejects_unparseable(owner_client, fake_state):
    r = await owner_client.post("/api/cod/import-payout",
                                files={"file": ("junk.csv", b"\x00\x01\x02 no rows here", "text/csv")})
    assert r.status_code == 400


async def test_import_payout_requires_staff(client, fake_state):
    r = await client.post("/api/cod/import-payout",
                          files={"file": ("p.csv", b"awb,cod\n1,2\n", "text/csv")})
    assert r.status_code in (401, 403)
