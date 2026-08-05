# -*- coding: utf-8 -*-
"""Barsy „Сметки" (accounts — Продажби по сметки) — READ-ONLY. Barsy is the system of record for
sales/accounts; the panel only mirrors them.

Captured live 2026-08-05 (read-only probe, 19 live accounts): list via
`Accounts_getlist {filters, length}` (a JSON list), per-account detail via `Accounts_get {account_id}`.
There is intentionally NO write path here — account/sale lifecycle is owned by Barsy (and, for client
orders, the connector identity, see #82). This read surface feeds #72 УНП capture.
"""
from __future__ import annotations

from ..core import barsy


def list_accounts(limit: int = 100) -> list[dict]:
    """The accounts list (most-recent first as Barsy returns them). `limit` caps the page."""
    r = barsy.call("Accounts_getlist", {"filters": {}, "length": int(limit)})
    return r if isinstance(r, list) else []


def get_account(account_id: int) -> dict:
    """Full detail for one account, or {} if not found."""
    r = barsy.call("Accounts_get", {"account_id": int(account_id)})
    return r if isinstance(r, dict) else {}
