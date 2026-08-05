# -*- coding: utf-8 -*-
"""Поща endpoints: role gate, validation, config-missing 503, HTML sanitize, send seam.
IMAP/SMTP internals get their RESULT gate LIVE on the box (real orders@ inbox) — these tests
cover the panel surface with the sync workers stubbed at the module seam."""
import pytest

from app.routers import mail as mail_mod


@pytest.fixture()
def stubbed(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(mail_mod, "_folders_sync",
                        lambda: [{"name": "INBOX", "total": 18, "unread": 3}])
    monkeypatch.setattr(mail_mod, "_messages_sync", lambda folder, page, per: {
        "folder": folder, "page": page, "per": per, "total": 1,
        "messages": [{"uid": "18", "subject": "Потвърждаване на абонамент",
                      "from": "Дарлинг <orders@darlinghome.bg>", "to": "orders@darlinghome.bg",
                      "date": "2026-08-05T22:20:00+03:00", "seen": False}]})
    monkeypatch.setattr(mail_mod, "_read_sync", lambda folder, uid: {
        "uid": uid, "subject": "x", "from": "a@b.bg", "to": "orders@darlinghome.bg", "cc": "",
        "date": "", "message_id": "<m1@x>", "body_text": "здравейте", "body_html": "",
        "attachments": []})

    def fake_send(to, subject, body, reply_id):
        calls.update(to=to, subject=subject, body=body, reply=reply_id)
        return {"ok": True, "message_id": "<new@x>"}

    monkeypatch.setattr(mail_mod, "_send_sync", fake_send)
    return calls


@pytest.mark.asyncio
async def test_anonymous_is_rejected(client, stubbed):
    # NB: `client` must stay unauthenticated here — deriving staff_client from it in the same
    # test upgrades it to an owner session (cookie persistence in the fixture chain).
    assert (await client.get("/api/mail/folders")).status_code == 401


@pytest.mark.asyncio
async def test_staff_can_read(staff_client, stubbed):
    assert (await staff_client.get("/api/mail/folders")).json()["folders"][0]["name"] == "INBOX"


@pytest.mark.asyncio
async def test_messages_and_read_shapes(staff_client, stubbed):
    r = (await staff_client.get("/api/mail/messages?folder=INBOX&page=1")).json()
    assert r["total"] == 1 and r["messages"][0]["subject"].startswith("Потвърждаване")
    assert (await staff_client.get("/api/mail/messages/18?folder=INBOX")).json()["body_text"] == "здравейте"
    assert (await staff_client.get("/api/mail/messages/abc")).status_code == 400
    assert (await staff_client.get("/api/mail/messages?folder=..%2Fetc")).status_code == 400
    assert (await staff_client.get("/api/mail/messages?folder=a%00b")).status_code == 400


@pytest.mark.asyncio
async def test_send_validates_and_forwards(staff_client, stubbed):
    r = await staff_client.post("/api/mail/send", json={
        "to": " klient@primer.bg ", "subject": "Отговор", "body": "Здравейте!",
        "reply_message_id": "<m1@x>"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert stubbed["to"] == "klient@primer.bg" and stubbed["reply"] == "<m1@x>"
    bad = await staff_client.post("/api/mail/send", json={
        "to": "not-an-email", "subject": "s", "body": "b"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_missing_config_is_honest_503(staff_client, monkeypatch):
    monkeypatch.setattr(mail_mod.courier_mod, "_cred", lambda k: "")
    assert (await staff_client.get("/api/mail/folders")).status_code == 503


def test_sanitizer_strips_active_content():
    dirty = ('<p onclick="x()">hi</p><script>evil()</script><style>a{}</style>'
             '<a href="javascript:boom()">l</a><iframe src="x"></iframe>')
    clean = mail_mod._sanitize_html(dirty)
    assert "<script" not in clean and "<style" not in clean and "<iframe" not in clean
    assert "onclick" not in clean and "javascript:" not in clean
    assert "<p" in clean and "hi" in clean
