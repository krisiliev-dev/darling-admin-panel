# -*- coding: utf-8 -*-
"""Заявки — per-supplier purchase requests: insight cards, add/qty/remove (with multi-supplier
prompt + duplicate-bump), supplier catalogue search, export, and Gmail-gated email send."""
from types import SimpleNamespace

import pytest

from app.routers import zayavki

# real anchors from the bundled supplier catalogue (АНИС supplies code 14153)
SUP = "АНИС"
CODE = "14153"


@pytest.mark.asyncio
async def test_insights_cards(owner_client):
    r = await owner_client.get("/api/zayavki/insights")
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    keys = {c["key"] for c in cards}
    assert {"reorder", "top_ordered", "top_earners"} <= keys
    top = next(c for c in cards if c["key"] == "top_ordered")
    assert len(top["products"]) > 0
    p = top["products"][0]
    assert p["code"] and "suggested_qty" in p and "suppliers" in p


@pytest.mark.asyncio
async def test_add_bumps_qty_and_totals(owner_client):
    r = await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 2})
    assert r.status_code == 200, r.text
    z = r.json()
    assert z["supplier"] == SUP and z["items"] == 1
    line = z["lines"][0]
    assert line["code"] == CODE and line["qty"] == 2
    assert line["delivery_price"] > 0            # auto-filled from the supplier mapping
    # add the same product again → qty bumps, still one line
    z2 = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 3})).json()
    assert z2["items"] == 1 and z2["lines"][0]["qty"] == 5
    assert z2["total"] == round(5 * line["delivery_price"], 2)


@pytest.mark.asyncio
async def test_add_without_supplier_single_resolves(owner_client):
    # 14153 has exactly one supplier → resolves without a prompt
    r = await owner_client.post("/api/zayavki/add", json={"code": CODE, "qty": 1})
    assert r.status_code == 200, r.text
    assert r.json()["supplier"] == SUP


@pytest.mark.asyncio
async def test_edit_and_remove_line(owner_client):
    z = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 4})).json()
    lid = z["lines"][0]["id"]
    z2 = (await owner_client.patch(f"/api/zayavki/lines/{lid}", json={"qty": 10})).json()
    assert z2["lines"][0]["qty"] == 10
    z3 = (await owner_client.patch(f"/api/zayavki/lines/{lid}", json={"qty": 0})).json()  # zero removes
    assert z3["items"] == 0


@pytest.mark.asyncio
async def test_suppliers_list_reflects_open_zayavka(owner_client):
    await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 2})
    rows = (await owner_client.get("/api/zayavki/suppliers")).json()["suppliers"]
    anis = next(s for s in rows if s["supplier"] == SUP)
    assert anis["catalog_count"] > 0 and anis["items"] == 1 and anis["total"] > 0


@pytest.mark.asyncio
async def test_supplier_catalog_search(owner_client):
    r = await owner_client.get(f"/api/zayavki/supplier/{SUP}/catalog", params={"q": "закачалка"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 0
    if body["products"]:
        assert "delivery_price" in body["products"][0]


@pytest.mark.asyncio
async def test_export_csv_and_xlsx(owner_client):
    z = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 2})).json()
    zid = z["id"]
    csv_r = await owner_client.get(f"/api/zayavki/zayavka/{zid}/export", params={"fmt": "csv"})
    assert csv_r.status_code == 200
    assert "text/csv" in csv_r.headers["content-type"]
    assert "attachment" in csv_r.headers["content-disposition"]
    xlsx_r = await owner_client.get(f"/api/zayavki/zayavka/{zid}/export", params={"fmt": "xlsx"})
    assert xlsx_r.status_code == 200
    assert xlsx_r.content[:2] == b"PK"          # a real xlsx (zip) file


@pytest.mark.asyncio
async def test_export_neutralises_formula_injection(owner_client):
    # a product name starting with '=' must be written as text, not a live formula (the file is
    # emailed to external suppliers)
    await owner_client.post("/api/zayavki/add",
                            json={"code": CODE, "supplier": SUP, "qty": 1, "name": "=HYPERLINK(0)"})
    z = next(s for s in (await owner_client.get("/api/zayavki/suppliers")).json()["suppliers"] if s["supplier"] == SUP)
    csv_r = await owner_client.get(f"/api/zayavki/zayavka/{z['open_id']}/export", params={"fmt": "csv"})
    assert "'=HYPERLINK(0)" in csv_r.text and "\n=HYPERLINK" not in csv_r.text


@pytest.mark.asyncio
async def test_qty_delta_is_atomic(owner_client):
    z = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 2})).json()
    lid = z["lines"][0]["id"]
    z2 = (await owner_client.patch(f"/api/zayavki/lines/{lid}", json={"delta": 1})).json()
    assert z2["lines"][0]["qty"] == 3           # applied to stored qty, not a stale absolute
    z3 = (await owner_client.patch(f"/api/zayavki/lines/{lid}", json={"delta": -3})).json()
    assert z3["items"] == 0                      # dropped to zero → removed


@pytest.mark.asyncio
async def test_supplier_prefs_persist_and_flag_below_min(owner_client):
    # set a minimum-order + email for the supplier
    r = await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}",
                                 json={"email": "anis@x.bg", "min_order": 500, "lead_days": 7})
    assert r.status_code == 200, r.text
    # a small заявка (below 500 €) → the supplier row flags below_min + carries the pref
    await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 1})
    row = next(s for s in (await owner_client.get("/api/zayavki/suppliers")).json()["suppliers"]
               if s["supplier"] == SUP)
    assert row["email"] == "anis@x.bg" and row["min_order"] == 500 and row["lead_days"] == 7
    assert row["below_min"] is True


@pytest.mark.asyncio
async def test_supplier_website_normalised_and_history(owner_client):
    # a bare domain is normalised to https://; a non-http scheme is rejected to ""
    r = await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}",
                                 json={"website": "anis.bg", "phone": "0888 123 456"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["website"] == "https://anis.bg" and body["phone"] == "0888 123 456"
    bad = await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}",
                                   json={"website": "javascript:alert(1)"})
    assert bad.json()["website"] == ""
    # the website surfaces on the suppliers list
    await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}", json={"website": "https://anis.bg"})
    row = next(s for s in (await owner_client.get("/api/zayavki/suppliers")).json()["suppliers"]
               if s["supplier"] == SUP)
    assert row["website"] == "https://anis.bg"
    # history endpoint responds with a (possibly empty) list for the supplier
    h = await owner_client.get(f"/api/zayavki/supplier/{SUP}/history")
    assert h.status_code == 200, h.text
    assert h.json()["supplier"] == SUP and isinstance(h.json()["zayavki"], list)


@pytest.mark.asyncio
async def test_no_supplier_nudge(owner_client):
    r = await owner_client.get("/api/zayavki/no-supplier")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "count" in body and isinstance(body["products"], list)


@pytest.mark.asyncio
async def test_catalog_flags_dead_stock(owner_client):
    r = await owner_client.get(f"/api/zayavki/supplier/{SUP}/catalog", params={"q": ""})
    assert r.status_code == 200
    if r.json()["products"]:
        assert "dead" in r.json()["products"][0]   # don't-reorder guard field present


@pytest.mark.asyncio
async def test_send_email_refuses_when_not_configured(owner_client, monkeypatch):
    import app.mailer as mailer
    monkeypatch.setattr(mailer, "get_settings",
                        lambda: SimpleNamespace(gmail_user="", gmail_app_password=""))
    z = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 1})).json()
    r = await owner_client.post(f"/api/zayavki/zayavka/{z['id']}/send-email",
                                json={"to": "x@y.bg", "confirm": True})
    assert r.status_code == 503
    assert "имейл" in r.json()["detail"].lower() or "gmail" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_send_email_sends_when_configured(owner_client, monkeypatch):
    # the endpoint does `from ..mailer import send_mail, mail_configured` at call time, so patching
    # the mailer module's attributes is enough.
    import app.mailer as mailer
    monkeypatch.setattr(mailer, "get_settings",
                        lambda: SimpleNamespace(gmail_user="darling7.sv@gmail.com", gmail_app_password="x",
                                                smtp_host="smtp.gmail.com", smtp_port=587,
                                                order_from_name="Дарлинг 7"))
    sent = {}
    async def _fake_send(**kw):
        sent.update(kw)
    monkeypatch.setattr(mailer, "send_mail", _fake_send)

    z = (await owner_client.post("/api/zayavki/add", json={"code": CODE, "supplier": SUP, "qty": 2})).json()
    # gate: confirm required
    unconf = await owner_client.post(f"/api/zayavki/zayavka/{z['id']}/send-email", json={"to": "sup@x.bg"})
    assert unconf.status_code == 400
    r = await owner_client.post(f"/api/zayavki/zayavka/{z['id']}/send-email",
                                json={"to": "sup@x.bg", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["sent_to"] == "sup@x.bg"
    assert sent["to"] == "sup@x.bg" and sent["attachment"][0].endswith(".xlsx")
    # zayavka is now marked sent
    detail = (await owner_client.get(f"/api/zayavki/zayavka/{z['id']}")).json()
    assert detail["status"] == "sent" and detail["sent_via"] == "email"


# --- supplier identity → Barsy reflection (Suppliers_create/update) ---
def test_supplier_data_body_maps_fields_and_folds_contacts():
    from app.models import SupplierPref
    from app.routers.zayavki import _supplier_data
    p = SupplierPref(supplier="АНИС", email="a@x.bg", website="https://anis.bg",
                     phone="0888", notes="стара бележка", official_name="АНИС ЕООД",
                     bulstat="123456789", vat_num="BG123456789", mol="Иван Иванов",
                     address="ул. Роза 1")
    d = _supplier_data(p, "АНИС")
    assert d["supplier_name"] == "АНИС ЕООД"        # legal name preferred
    assert d["bulstat"] == "123456789" and d["vat_num"] == "BG123456789"
    assert d["mol"] == "Иван Иванов" and d["address"] == "ул. Роза 1"
    assert d["email"] == "a@x.bg"
    # website + phone have no native SupplierData field → folded into notes (Barsy gotcha)
    assert "Уебсайт: https://anis.bg" in d["notes"] and "Тел: 0888" in d["notes"]
    assert "стара бележка" in d["notes"]


def test_supplier_data_falls_back_to_display_name():
    from app.models import SupplierPref
    from app.routers.zayavki import _supplier_data
    d = _supplier_data(SupplierPref(supplier="ГАМА"), "ГАМА")
    assert d["supplier_name"] == "ГАМА"
    assert "bulstat" not in d and "notes" not in d   # nothing set → minimal body


@pytest.mark.asyncio
async def test_push_supplier_gated_dry_run(owner_client, monkeypatch):
    monkeypatch.setattr(zayavki, "barsy_write_live", lambda: False)
    await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}",
                             json={"official_name": f"{SUP} ЕООД", "bulstat": "123"})
    r = await owner_client.post(f"/api/zayavki/supplier/{SUP}/push-barsy")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["gated"] is True and b["action"] == "create"
    assert b["would_send"]["supplier_name"] == f"{SUP} ЕООД"


@pytest.mark.asyncio
async def test_push_supplier_create_then_update(owner_client, barsy_recorder, monkeypatch):
    monkeypatch.setattr(zayavki, "barsy_write_live", lambda: True)
    barsy_recorder.set_return("Suppliers_create", 42)   # Barsy returns the new supplier_id
    await owner_client.patch(f"/api/zayavki/supplier-prefs/{SUP}", json={"bulstat": "999"})

    r1 = await owner_client.post(f"/api/zayavki/supplier/{SUP}/push-barsy")
    assert r1.status_code == 200 and r1.json()["action"] == "create" and r1.json()["supplier_id"] == 42
    assert barsy_recorder.body_for("Suppliers_create")["supplier"]["bulstat"] == "999"

    # a second push now UPDATES by the stored id, not create
    r2 = await owner_client.post(f"/api/zayavki/supplier/{SUP}/push-barsy")
    assert r2.json()["action"] == "update" and r2.json()["supplier_id"] == 42
    assert barsy_recorder.body_for("Suppliers_update")["supplier_id"] == 42
