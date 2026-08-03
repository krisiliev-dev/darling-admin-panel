# -*- coding: utf-8 -*-
"""Заявка → Barsy `requests`.

The contract these lock down is fail-closed-on-goods: an order to nobody, an order missing lines,
or an order sent while the write gate is off must not happen quietly. They assert the BODY that
would reach Barsy, because that body is the order.
"""
import json

import pytest
from sqlalchemy import select


async def _mk_zayavka(*, supplier_id=39, codes=("0110375", "018223"), email="dostavchik@example.com"):
    from app.db import async_session_maker
    from app.models import ProductCache, Zayavka, ZayavkaLine
    async with async_session_maker() as s:
        s.add(ProductCache(article_id=1101, code="0110375", name="Bormioli Rocco - чаша 250 мл"))
        s.add(ProductCache(article_id=1802, code="018223", name="Овална лъжица"))
        z = Zayavka(supplier="Мира-Н", supplier_id=supplier_id, supplier_email=email,
                    status="draft", notes="за петък")
        s.add(z)
        await s.flush()
        for i, c in enumerate(codes):
            s.add(ZayavkaLine(zayavka_id=z.id, code=c, supplier_code=f"S{i}", name=f"line {c}",
                              qty=3 + i, delivery_price=2.5))
        await s.commit()
        return z.id


@pytest.mark.asyncio
async def test_gated_push_returns_the_exact_body_and_writes_nothing(owner_client, monkeypatch):
    """With BARSY_WRITE_LIVE off the operator gets the order to read, and Barsy is never called."""
    import app.routers.zayavki as z_router
    called = []
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: False)
    monkeypatch.setattr(z_router.barsy_requests, "save", lambda body: called.append(body) or 7)

    zid = await _mk_zayavka()
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dry_run"] is True and out["gated"] is True
    assert called == [], "the gate must mean NO Barsy call at all"

    body = out["would_send"]
    assert body["id"] is None                       # create, not update
    assert body["action_type"] == "save"
    assert body["values"]["supplier_id"] == 39
    assert len(body["rows"]) == 2
    row = body["rows"][0]
    assert row["article_id"] == 1101
    assert row["amount"] == "3"                     # Barsy's own UI sends strings
    assert row["package_amount"] == "3"             # no разфасовка → mirrors the amount


@pytest.mark.asyncio
async def test_a_line_with_no_barsy_article_refuses_the_whole_order(owner_client, monkeypatch):
    """A supplier order that silently drops lines is worse than no order."""
    import app.routers.zayavki as z_router
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: False)
    zid = await _mk_zayavka(codes=("0110375", "НЯМА-ТАКЪВ"))
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={})
    assert r.status_code == 400
    assert "НЯМА-ТАКЪВ" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_supplier_not_linked_to_barsy_is_refused(owner_client, monkeypatch):
    import app.routers.zayavki as z_router
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: False)
    zid = await _mk_zayavka(supplier_id=None)
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={})
    assert r.status_code == 400
    assert "Barsy" in r.json()["detail"]


@pytest.mark.asyncio
async def test_an_empty_zayavka_is_refused(owner_client, monkeypatch):
    import app.routers.zayavki as z_router
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: False)
    zid = await _mk_zayavka(codes=())
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_a_live_push_needs_confirm_and_then_records_the_request_id(owner_client, monkeypatch):
    import app.routers.zayavki as z_router
    from app.db import async_session_maker
    from app.models import Zayavka
    saved = []
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: True)
    monkeypatch.setattr(z_router.barsy_requests, "save", lambda body: saved.append(body) or 42)

    zid = await _mk_zayavka()
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={})
    assert r.status_code == 400, "a real ERP write must be confirmed"
    assert saved == []

    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["barsy_request_id"] == 42
    async with async_session_maker() as s:
        z = (await s.execute(select(Zayavka).where(Zayavka.id == zid))).scalar_one()
        assert z.barsy_request_id == 42


@pytest.mark.asyncio
async def test_a_second_push_updates_the_same_request_instead_of_ordering_twice(owner_client, monkeypatch):
    import app.routers.zayavki as z_router
    bodies = []
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: True)
    monkeypatch.setattr(z_router.barsy_requests, "save", lambda body: bodies.append(body) or 42)

    zid = await _mk_zayavka()
    await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={"confirm": True})
    await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy", json={"confirm": True})
    assert len(bodies) == 2
    assert bodies[0]["id"] is None      # first push creates
    assert bodies[1]["id"] == 42        # second UPDATES that request


@pytest.mark.asyncio
async def test_sending_through_barsy_marks_the_zayavka_sent(owner_client, monkeypatch):
    import app.routers.zayavki as z_router
    from app.db import async_session_maker
    from app.models import Zayavka
    sends = []
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: True)
    monkeypatch.setattr(z_router.barsy_requests, "save", lambda body: 42)
    monkeypatch.setattr(z_router.barsy_requests, "open_send_dialog", lambda rid: {})
    monkeypatch.setattr(z_router.barsy_requests, "send",
                        lambda rid, **kw: sends.append((rid, kw)) or {"ok": True})

    zid = await _mk_zayavka()
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy",
                                json={"confirm": True, "send": True})
    assert r.status_code == 200, r.text
    assert r.json()["sent_to"] == "dostavchik@example.com"
    assert sends and sends[0][0] == 42
    # Barsy's own PDF is the point of sending through Barsy
    assert sends[0][1]["to"] == "dostavchik@example.com"
    async with async_session_maker() as s:
        z = (await s.execute(select(Zayavka).where(Zayavka.id == zid))).scalar_one()
        assert z.status == "sent" and z.sent_via == "barsy" and z.sent_at is not None


@pytest.mark.asyncio
async def test_send_without_a_supplier_email_says_what_did_and_did_not_happen(owner_client, monkeypatch):
    """The заявка IS in Barsy by then — the error must not imply otherwise."""
    import app.routers.zayavki as z_router
    monkeypatch.setattr(z_router, "barsy_write_live", lambda: True)
    monkeypatch.setattr(z_router.barsy_requests, "save", lambda body: 43)
    zid = await _mk_zayavka(email="")
    r = await owner_client.post(f"/api/zayavki/zayavka/{zid}/to-barsy",
                                json={"confirm": True, "send": True})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "43" in detail and "не е изпратена" in detail


@pytest.mark.asyncio
async def test_the_row_helper_matches_the_captured_contract():
    """Field-for-field against docs/BARSY_ZAYAVKI_CONTRACT.md — these names came off the wire."""
    from app.services import barsy_requests
    r = barsy_requests.row(11365, "Доставка", 3)
    assert set(r) == {"article_id", "article_name", "amount", "package_id", "package_amount",
                      "delivery_price", "detail_notes"}
    b = barsy_requests.build_body(39, "МИРА-Н", [r])
    assert set(b) == {"id", "action_type", "values", "rows"}
    assert set(b["values"]) == {"user_name", "expected_delivery_date", "ordering_view",
                                "supplier_id", "supplier_name", "description", "notes",
                                "depot_id", "client_order_id"}
    assert b["values"]["ordering_view"] == "0"
    assert b["values"]["depot_id"] == ""      # "" = Всички складове
    json.dumps(b)                              # must be JSON-serialisable as-is
