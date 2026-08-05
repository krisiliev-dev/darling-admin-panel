# -*- coding: utf-8 -*-
"""Поща — the orders@ mailbox inside the panel (owner decision 2026-08-05: „everything in one
place"; mail first, then 2FA, then wider unification).

Talks to OUR mail server (docker-mailserver on the woo box) over IMAPS 993 (read) and
submission 587 STARTTLS (send) — the same protocols a mail app would use; nothing new to run.
Credentials ride in ~/.hermes/.env (MAIL_HOST / MAIL_IMAP_PORT / MAIL_SMTP_PORT / MAIL_USER /
MAIL_ORDERS_PASSWORD) via the same _cred() path as the courier keys — never in the repo.

Design rules:
- Staff and up can read/reply (customer service IS the point); viewer/packer stay out.
- One IMAP connection per request, in a threadpool — stateless and simple; fine at our scale.
- HTML bodies are sanitized server-side (scripts/handlers/javascript: stripped) AND the
  frontend renders them in a sandboxed iframe — belt and braces; mail is hostile input.
- Sending APPENDs a copy to the Sent folder so the mailbox stays the single source of truth
  for what was said to a customer, regardless of which surface sent it.
- Missing credentials → honest 503 („не е свързана"), never a crash.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default as email_policy

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..core import courier as courier_mod
from ..models import User
from ..users import require_roles

router = APIRouter(tags=["mail"])

_FOLDER_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+"?(?P<name>[^"]+)"?$')
_ALLOWED_FOLDER = re.compile(r"^[\w .\-/\[\]&#~]+$")


def _cfg() -> dict:
    host = courier_mod._cred("MAIL_HOST")
    user = courier_mod._cred("MAIL_USER")
    pw = courier_mod._cred("MAIL_ORDERS_PASSWORD")
    if not (host and user and pw):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Пощата не е свързана (липсват MAIL_* настройки).")
    return {"host": host, "user": user, "pw": pw,
            "imap_port": int(courier_mod._cred("MAIL_IMAP_PORT") or 993),
            "smtp_port": int(courier_mod._cred("MAIL_SMTP_PORT") or 587)}


def _imap(cfg: dict) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(cfg["host"], cfg["imap_port"], ssl_context=ssl.create_default_context())
    m.login(cfg["user"], cfg["pw"])
    return m


def _dec(value) -> str:
    if value is None:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(str(value))))
    except Exception:
        return str(value)


def _sanitize_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|iframe|object|embed|form)\b.*?</\1>", "", html)
    html = re.sub(r"(?is)<(script|style|iframe|object|embed|form)\b[^>]*/?>", "", html)
    html = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html)
    html = re.sub(r"(?i)(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*", r"\1=\2#", html)
    return html


def _check_folder(folder: str) -> str:
    if not _ALLOWED_FOLDER.match(folder or "") or ".." in folder:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалидна папка.")
    return folder


# ------------------------------------------------------------------ sync workers ---
def _folders_sync() -> list[dict]:
    cfg = _cfg()
    m = _imap(cfg)
    try:
        out = []
        typ, rows = m.list()
        for raw in rows or []:
            s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            mm = _FOLDER_RE.search(s)
            if not mm or r"\Noselect" in mm.group("flags"):
                continue
            name = mm.group("name")
            unread = total = 0
            typ, st = m.status(f'"{name}"', "(MESSAGES UNSEEN)")
            if typ == "OK" and st and st[0]:
                nums = re.search(r"MESSAGES\s+(\d+)\s+UNSEEN\s+(\d+)",
                                 st[0].decode("utf-8", "replace"))
                if nums:
                    total, unread = int(nums.group(1)), int(nums.group(2))
            out.append({"name": name, "total": total, "unread": unread})
        order = {"INBOX": 0, "Sent": 1, "Drafts": 2, "Junk": 3, "Trash": 4}
        out.sort(key=lambda f: (order.get(str(f["name"]), 9), str(f["name"])))
        return out
    finally:
        m.logout()


def _messages_sync(folder: str, page: int, per: int) -> dict:
    cfg = _cfg()
    m = _imap(cfg)
    try:
        typ, data = m.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава папка.")
        typ, d = m.uid("SEARCH", "ALL")
        uids = [u.decode() for u in (d[0] or b"").split()]
        uids.reverse()                                   # newest first
        total = len(uids)
        chunk = uids[(page - 1) * per: page * per]
        rows = []
        for uid in chunk:
            typ, fd = m.uid("FETCH", uid,
                            "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])")
            if typ != "OK" or not fd or fd[0] is None:
                continue
            flags = " ".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)
                             for x in fd if not isinstance(x, tuple))
            hdr = email.message_from_bytes(fd[0][1], policy=email_policy)
            dt = (email.utils.parsedate_to_datetime(str(hdr.get("Date")))
                  if hdr.get("Date") else None)
            rows.append({"uid": uid, "subject": _dec(hdr.get("Subject")) or "(без тема)",
                         "from": _dec(hdr.get("From")), "to": _dec(hdr.get("To")),
                         "date": dt.isoformat() if dt else "",
                         "seen": "\\Seen" in flags})
        return {"folder": folder, "page": page, "per": per, "total": total, "messages": rows}
    finally:
        m.logout()


def _read_sync(folder: str, uid: str) -> dict:
    cfg = _cfg()
    m = _imap(cfg)
    try:
        typ, _ = m.select(f'"{folder}"')
        if typ != "OK":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такава папка.")
        typ, fd = m.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not fd or fd[0] is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такова писмо.")
        msg = email.message_from_bytes(fd[0][1], policy=email_policy)
        text_part = msg.get_body(preferencelist=("plain",))
        html_part = msg.get_body(preferencelist=("html",))
        body_text = text_part.get_content() if text_part else ""
        body_html = _sanitize_html(html_part.get_content()) if html_part else ""
        m.uid("STORE", uid, "+FLAGS", r"(\Seen)")
        dt = (email.utils.parsedate_to_datetime(str(msg.get("Date")))
              if msg.get("Date") else None)
        atts = [{"filename": p.get_filename() or "attachment",
                 "content_type": p.get_content_type(), "size": len(p.get_payload(decode=True) or b"")}
                for p in msg.iter_attachments()]
        return {"uid": uid, "subject": _dec(msg.get("Subject")) or "(без тема)",
                "from": _dec(msg.get("From")), "to": _dec(msg.get("To")),
                "cc": _dec(msg.get("Cc")), "date": dt.isoformat() if dt else "",
                "message_id": msg.get("Message-ID", ""),
                "body_text": body_text, "body_html": body_html,
                "attachments": atts}
    finally:
        m.logout()


def _send_sync(to: str, subject: str, body: str, reply_message_id: str) -> dict:
    cfg = _cfg()
    msg = EmailMessage()
    msg["From"] = f"Дарлинг <{cfg['user']}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=cfg["host"].split(".", 1)[-1])
    if reply_message_id:
        msg["In-Reply-To"] = reply_message_id
        msg["References"] = reply_message_id
    msg.set_content(body)
    with smtplib.SMTP(cfg["host"], cfg["smtp_port"], timeout=25) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(cfg["user"], cfg["pw"])
        s.send_message(msg)
    # keep the mailbox the single source of truth: file a copy in Sent
    try:
        m = _imap(cfg)
        try:
            m.append('"Sent"', r"(\Seen)", imaplib.Time2Internaldate(email.utils.localtime()),
                     msg.as_bytes())
        finally:
            m.logout()
    except Exception:
        pass                                             # send succeeded; Sent copy is best-effort
    return {"ok": True, "message_id": msg["Message-ID"]}


# ------------------------------------------------------------------ endpoints ---
class SendIn(BaseModel):
    to: str = Field(min_length=3, max_length=500)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    reply_message_id: str = Field(default="", max_length=300)


@router.get("/api/mail/folders")
async def folders(user: User = Depends(require_roles("staff"))) -> dict:
    return {"folders": await run_in_threadpool(_folders_sync)}


@router.get("/api/mail/messages")
async def messages(folder: str = Query("INBOX"), page: int = Query(1, ge=1),
                   per: int = Query(25, ge=1, le=100),
                   user: User = Depends(require_roles("staff"))) -> dict:
    return await run_in_threadpool(_messages_sync, _check_folder(folder), page, per)


@router.get("/api/mail/messages/{uid}")
async def read_message(uid: str, folder: str = Query("INBOX"),
                       user: User = Depends(require_roles("staff"))) -> dict:
    if not re.match(r"^\d+$", uid):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден uid.")
    return await run_in_threadpool(_read_sync, _check_folder(folder), uid)


@router.post("/api/mail/send")
async def send(payload: SendIn, user: User = Depends(require_roles("staff"))) -> dict:
    tos = [a.strip() for a in payload.to.split(",") if a.strip()]
    if not tos or not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", a) for a in tos):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден адрес на получател.")
    return await run_in_threadpool(_send_sync, ", ".join(tos), payload.subject.strip(),
                                   payload.body, payload.reply_message_id.strip())
