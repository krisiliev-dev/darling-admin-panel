# -*- coding: utf-8 -*-
"""Фактури за издаване — the B2B invoice EXCEPTION queue.

Invoices are issued AUTOMATICALLY by the connector at finalise (via the undocumented `Invoices_save`,
whose contract was captured from Barsy's own admin UI). This screen therefore holds only what automation
could not complete — almost always incomplete company data — plus the record of what staff issued by hand
in Документи → Фактури → „Нова фактура по сметка(и)".

The two failures worth pinning: a legally-owed invoice silently VANISHING, and the same sale being
invoiced TWICE (once automatically, once by a human reading a stale screen)."""
import app.routers.invoices as inv

_REQ = {
    "order_id": "7718876578133", "order_ref": "#1010", "account_id": "32", "amount": 18.49,
    "company": {"name": "АКМЕ ЕООД", "eik": "123456789", "vat": "BG123456789",
                "mol": "Иван Петров", "city": "София", "address": "ул. Роза 1"},
}


def _patch(monkeypatch, rows):
    monkeypatch.setattr(inv.connector, "list_invoice_requests", lambda: list(rows), raising=False)


async def test_lists_pending_with_the_company_block_and_account(owner_client, monkeypatch):
    _patch(monkeypatch, [_REQ])
    b = (await owner_client.get("/api/invoices")).json()
    assert b["pending_count"] == 1 and b["issued_count"] == 0
    row = b["pending"][0]
    # account_id is what staff type into „Нова фактура по сметка(и)" — it must survive the round-trip
    assert row["account_id"] == "32" and row["order_ref"] == "#1010"
    assert row["company"]["eik"] == "123456789" and row["missing"] == []


async def test_missing_company_fields_are_flagged_not_hidden(owner_client, monkeypatch):
    # a фактура without a name/ЕИК isn't legally valid — staff must see that BEFORE opening Barsy
    _patch(monkeypatch, [{**_REQ, "company": {"vat": "BG1", "city": "София"}}])
    row = (await owner_client.get("/api/invoices")).json()["pending"][0]
    assert set(row["missing"]) == {"name", "eik"}
    assert row["company"]["name"] == "" and row["company"]["eik"] == ""   # stable keys for the UI


async def test_mark_issued_moves_it_out_of_pending(owner_client, monkeypatch):
    _patch(monkeypatch, [_REQ])
    r = await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued",
                                json={"invoice_number": "0000000042", "account_id": "32",
                                      "order_ref": "#1010", "company": "АКМЕ ЕООД", "confirm": True})
    assert r.status_code == 200, r.text
    b = (await owner_client.get("/api/invoices")).json()
    assert b["pending_count"] == 0 and b["issued_count"] == 1
    assert b["issued"][0]["invoice_number"] == "0000000042"


async def test_double_marking_keeps_the_first_number(owner_client, monkeypatch):
    """One фактура per order. A second click must NOT overwrite the recorded number — it reports the
    one already on file, so the audit trail can't be rewritten by a stray double-click."""
    _patch(monkeypatch, [_REQ])
    body = {"invoice_number": "0000000042", "confirm": True}
    assert (await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued", json=body)).status_code == 200
    r2 = await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued",
                                 json={"invoice_number": "0000000099", "confirm": True})
    assert r2.status_code == 200 and r2.json()["already"] is True
    assert r2.json()["invoice_number"] == "0000000042"
    assert (await owner_client.get("/api/invoices")).json()["issued"][0]["invoice_number"] == "0000000042"


async def test_mark_issued_requires_confirm_and_a_number(owner_client, monkeypatch):
    _patch(monkeypatch, [_REQ])
    no_confirm = await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued",
                                         json={"invoice_number": "42", "confirm": False})
    no_number = await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued",
                                        json={"invoice_number": "  ", "confirm": True})
    assert no_confirm.status_code == 400 and no_number.status_code == 400


async def test_missing_connector_fn_is_501_not_an_empty_list(owner_client, monkeypatch):
    # an empty list would read as "no invoices to issue" — a dangerous lie. Say it's unavailable.
    monkeypatch.delattr(inv.connector, "list_invoice_requests", raising=False)
    assert (await owner_client.get("/api/invoices")).status_code == 501


async def test_requires_auth(client):
    assert (await client.get("/api/invoices")).status_code == 401


# --- auto-issued invoices (connector Invoices_save) must never look pending -----------
async def test_connector_auto_issued_is_not_pending(owner_client, monkeypatch):
    """Invoices are issued AUTOMATICALLY at finalise. If the screen showed an already-issued order as
    'to issue', staff would create a SECOND фактура for the same sale — an accounting error, not noise."""
    _patch(monkeypatch, [{**_REQ, "issued": True, "invoice_number": "0000000002"}])
    b = (await owner_client.get("/api/invoices")).json()
    assert b["pending_count"] == 0 and b["issued_count"] == 1
    row = b["issued"][0]
    assert row["invoice_number"] == "0000000002" and row["auto"] is True


async def test_marking_an_auto_issued_order_is_refused(owner_client, monkeypatch):
    """Belt-and-braces for the same failure: even if the page was loaded before the connector issued it,
    recording a manual number must be REFUSED, not written over the existing фактура."""
    _patch(monkeypatch, [{**_REQ, "issued": True, "invoice_number": "0000000002"}])
    r = await owner_client.post(f"/api/invoices/{_REQ['order_id']}/issued",
                                json={"invoice_number": "0000000099", "confirm": True})
    assert r.status_code == 409
    assert "автоматично" in r.json()["detail"]
