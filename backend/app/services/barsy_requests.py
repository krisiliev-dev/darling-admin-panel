# -*- coding: utf-8 -*-
"""Barsy „Заявки - доставчици" — the `requests` entity.

Captured live from the Barsy admin UI on 2026-08-04 by hooking XHR and running a real
create → send → delete cycle; the full transcript is in docs/BARSY_ZAYAVKI_CONTRACT.md. Barsy's
docs do not describe this entity, so every field name here came off the wire, not a guess.

This supersedes the old assumption that Barsy has no supplier-order document. It does, and it is
NOT a storeload: a storeload is goods ARRIVING, a request is goods being ASKED FOR. Barsy's own
lifecycle keeps them apart — Нови → Изпратени → Заредени, where „Заредени" is the request being
converted into a storeload at receiving.

Everything here is a POST to /endpoints/json?bid=1 with the method name as the single top-level
key, same envelope as the rest of the API.
"""
from __future__ import annotations

from typing import Any

from ..core import barsy


def build_body(supplier_id: int, supplier_name: str, rows: list[dict], *,
               request_id: int | None = None, expected_delivery_date: str | None = None,
               description: str = "", notes: str = "", depot_id: str = "",
               client_order_id: int | None = None, user_name: str | None = None) -> dict:
    """The exact Requests_save payload, built separately from the call so the write-gated dry-run
    can show the operator what WOULD be sent (project rule: fail closed on money, and let a human
    read the body before it goes).

    `id: None` creates; an existing request_id updates. Barsy sends the numeric fields as strings
    from its own UI, and we match that rather than inventing a cleaner shape it has never seen.
    """
    return {
        "id": request_id,
        "action_type": "save",
        "values": {
            "user_name": user_name,
            "expected_delivery_date": expected_delivery_date,
            "ordering_view": "0",
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "description": description,
            "notes": notes,
            "depot_id": depot_id,          # "" = Всички складове
            "client_order_id": client_order_id,
        },
        "rows": rows,
    }


def _num(v) -> str:
    """Format a number the way Barsy's own UI does: "3", not "3.0"; "1.5" stays "1.5".

    Our quantities are floats, so a bare str() diverges from every payload we have actually seen
    Barsy accept. On a document that orders goods, matching the observed shape beats guessing that
    it tolerates ours.
    """
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def row(article_id: int, article_name: str, amount, *, package_id: str = "",
        package_amount=None, delivery_price="", detail_notes: str = "") -> dict:
    """One request line. `package_amount` (Кол. доставчик) defaults to the ordered amount, which is
    what the UI does when there is no разфасовка."""
    amt = _num(amount)
    return {
        "article_id": article_id,
        "article_name": article_name,
        "amount": amt,
        "package_id": package_id,
        "package_amount": amt if package_amount is None else _num(package_amount),
        "delivery_price": "" if delivery_price in (None, "") else _num(delivery_price),
        "detail_notes": detail_notes,
    }


def save(body: dict) -> int:
    """Create or update a заявка. Returns the request_id."""
    return int(barsy.call("Requests_save", body))


def open_send_dialog(request_id: int) -> dict:
    """Step one of sending: Barsy prefills the template and the supplier's email. We call it for
    the same reason the UI does — the prefill is where the supplier address and template come
    from, so sending without it means inventing both."""
    return barsy.call("Messagehub_send", {"id": request_id, "params": {"type": "request"}}) or {}


def send(request_id: int, *, to: str, subject: str, body_html: str, template_id: str = "2",
         service: str = "Email", attach_file: int = 1) -> Any:
    """Step two: actually send. `attach_file=1` attaches Barsy's own „Заявка за доставка" PDF,
    which is the point of sending through Barsy rather than from the panel — the supplier gets the
    ERP's document, not our re-rendering of it.

    Barsy expands these placeholders itself: $request_id$, $company_name$, $supplier_name$,
    $company_email$, $company_tel$, $company_fax$.
    """
    return barsy.call("messagehub_send", {
        "action_type": "save",
        "active_struct_id": "eStructForm_1",
        "params": {"type": "request"},
        "id": request_id,
        "values": {
            "to": to,
            "template_subject": subject,
            "service": service,          # also: По телефон, Отдалечени системи
            "template_id": template_id,
            "attach_file": attach_file,
            "template_body": body_html,
        },
    })


def delete(request_id: int) -> Any:
    """Delete a заявка. Barsy allows this ONLY while the заявка is still NEW — once sent it loses
    its delete action and stays as history. Verified: request #1 deleted, #2 (sent) could not be."""
    return barsy.call("requests_delete", {"id": request_id})
