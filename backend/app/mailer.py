# -*- coding: utf-8 -*-
"""Minimal SMTP mailer for outbound панел mail (заявки to suppliers). Uses the business Gmail via
an App Password (GMAIL_USER + GMAIL_APP_PASSWORD in env). Blocking smtplib runs off the event loop.

Security: this only ever sends what a panel user explicitly triggers (a confirmed заявка send). The
App Password is read from server config, never from request data."""
import smtplib
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from .config import get_settings


def mail_configured() -> bool:
    s = get_settings()
    return bool(s.gmail_user and s.gmail_app_password)


def _send_sync(to: str, subject: str, body: str, attachment, reply_to: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = f"{s.order_from_name} <{s.gmail_user}>" if s.order_from_name else s.gmail_user
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if attachment:
        fname, data, ctype = attachment
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                           filename=fname)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
        server.starttls()
        server.login(s.gmail_user, s.gmail_app_password)
        server.send_message(msg)


async def send_mail(to: str, subject: str, body: str, attachment=None, reply_to: str = "") -> None:
    """Send an email (optionally with one attachment=(filename, bytes, content_type))."""
    await run_in_threadpool(_send_sync, to, subject, body, attachment, reply_to)
