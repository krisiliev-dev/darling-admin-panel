# -*- coding: utf-8 -*-
"""ОРЗД/GDPR data-subject tool: look up + erase the panel's own data for a phone, retracting any
nekorekten report we published. Owner-only."""
import pytest

from app import nekorekten


async def _record(client, phone, outcome="refused", order="#1"):
    return await client.post("/api/risk/outcome", json={"phone": phone, "outcome": outcome, "order_name": order})


async def _cart(token, phone="", email="", name="#C1"):
    """Insert a Checkout (cart mirror) row directly — the panel's OTHER store of customer PII."""
    from app.db import async_session_maker
    from app.models import Checkout
    async with async_session_maker() as s:
        s.add(Checkout(token=token, phone=phone, email=email, name=name, item_count=1))
        await s.commit()


async def _cart_tokens() -> list[str]:
    from sqlalchemy import select

    from app.db import async_session_maker
    from app.models import Checkout
    async with async_session_maker() as s:
        return list((await s.execute(select(Checkout.token))).scalars().all())


@pytest.fixture(autouse=True)
def _nk(monkeypatch):
    monkeypatch.setattr(nekorekten, "check_phone", lambda p: {"enabled": True, "reports": 0})
    monkeypatch.setattr(nekorekten, "enabled", lambda: True)


async def test_lookup_shows_held_data(owner_client):
    await _record(owner_client, "0888123456")
    r = await owner_client.get("/api/gdpr/lookup?phone=%2B359888123456")
    assert r.status_code == 200
    b = r.json()
    assert b["phone"] == "359888123456" and b["count"] == 1
    assert b["outcomes"][0]["outcome"] == "refused"


async def test_erase_deletes_and_retracts(owner_client, monkeypatch):
    deleted = []
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: deleted.append(rid) or {"ok": True})
    monkeypatch.setattr(nekorekten, "report", lambda *a, **k: {"ok": True, "report_id": "R1"})
    await _record(owner_client, "0888123456", order="#7")
    await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "Отказана поръчка тук.", "confirm": True})

    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888123456", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["erased"] >= 1 and body["reports_retracted"] == 1 and deleted == ["R1"]
    # data is gone
    assert (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()["count"] == 0


async def test_erase_keeps_row_when_retraction_fails(owner_client, monkeypatch):
    """If the nekorekten retraction fails, the local row (which holds the report id) must NOT be
    deleted — otherwise the published PII is orphaned with no way to retract it. The failure is
    surfaced so the owner can retry."""
    monkeypatch.setattr(nekorekten, "delete_report", lambda rid: {"ok": False, "error": "5xx"})
    monkeypatch.setattr(nekorekten, "report", lambda *a, **k: {"ok": True, "report_id": "R9"})
    await _record(owner_client, "0888123456", order="#9")
    await owner_client.post("/api/risk/report", json={"phone": "0888123456", "text": "Отказана поръчка тук.", "confirm": True})

    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888123456", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["reports_retracted"] == 0
    assert body.get("retraction_failed")               # failure surfaced
    # the reported row is retained (still holds R9 for a retry), so lookup still finds it
    assert (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()["count"] == 1


async def test_lookup_includes_cart_mirror(owner_client):
    """The abandoned-cart mirror holds email/phone/name — an Art.15 export that omits it under-reports
    the PII the panel holds. Checkout.phone is the RAW Shopify value, so matching must normalize."""
    await _cart("chk_gdpr", phone="+359 88 812 3456", email="Kupuvach@Example.BG")
    b = (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()
    assert b["checkouts_count"] == 1 and b["count"] == 1
    assert b["checkouts"][0]["token"] == "chk_gdpr"
    assert b["checkouts"][0]["email"] == "Kupuvach@Example.BG"


async def test_lookup_by_email_finds_phoneless_cart(owner_client):
    # Shopify collects the email before the phone → email-only carts must still be findable.
    await _cart("chk_mail", email="a@b.bg")
    b = (await owner_client.get("/api/gdpr/lookup?phone=&email=A@B.bg")).json()
    assert b["checkouts_count"] == 1


async def test_erase_removes_cart_pii(owner_client):
    """GDPR Art.17: /erase must purge the Checkout row too, not just the refusal history — otherwise
    we report success while the customer's email/phone stay queryable via /api/carts."""
    await _record(owner_client, "0888123456", order="#11")
    await _cart("chk_mine", phone="0888123456", email="mine@b.bg")
    await _cart("chk_other", phone="0899999999", email="other@b.bg")

    body = (await owner_client.post("/api/gdpr/erase",
                                    json={"phone": "0888123456", "confirm": True})).json()
    assert body["erased"] == 1 and body["checkouts_erased"] == 1
    assert await _cart_tokens() == ["chk_other"]          # only the data subject's cart is gone
    assert (await owner_client.get("/api/gdpr/lookup?phone=0888123456")).json()["count"] == 0


async def test_erase_digitless_phone_does_not_mass_erase_carts(owner_client):
    # "n/a" normalizes to "" — it must not sweep the cart mirror (nor the phone-less rows in it).
    await _cart("chk_a", phone="0888123456")
    await _cart("chk_anon")                                # anonymous cart: no phone, no email
    body = (await owner_client.post("/api/gdpr/erase", json={"phone": "n/a", "confirm": True})).json()
    assert body["checkouts_erased"] == 0 and body.get("skipped")
    assert sorted(await _cart_tokens()) == ["chk_a", "chk_anon"]


async def test_erase_requires_confirm(owner_client):
    r = await owner_client.post("/api/gdpr/erase", json={"phone": "0888", "confirm": False})
    assert r.status_code == 400


async def test_gdpr_is_owner_only(staff_client):
    assert (await staff_client.get("/api/gdpr/lookup?phone=0888")).status_code in (401, 403)
    assert (await staff_client.post("/api/gdpr/erase", json={"phone": "0888", "confirm": True})).status_code in (401, 403)
