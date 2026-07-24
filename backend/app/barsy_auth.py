# -*- coding: utf-8 -*-
"""Centralized auth against Barsy. Barsy authenticates every API call with per-user HTTP Basic
auth, so we verify a panel login by calling Users_getcurrent AS that user: success returns their
profile (id, name, role); failure is an auth error. Passwords are never stored — Barsy generates
and emails them; the panel only verifies. The panel's OWN Barsy data calls keep using the service
account; a user's credentials are used only at login."""
import base64
import json
import urllib.error
import urllib.request

from .core import barsy


def _barsy_base() -> str:
    base, _, _ = barsy._env()
    return base


def verify(username: str, password: str) -> dict | None:
    """Return the Barsy user profile if the credentials are valid, else None.
    Raises RuntimeError only on a transport/availability problem (so login can distinguish
    'wrong password' from 'Barsy down')."""
    if not username or not password:
        return None
    tok = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{_barsy_base()}/Users_getcurrent", data=b"[]", method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Basic " + tok})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # 4xx = request/credentials rejected → invalid login; 5xx = Barsy is down
        if 400 <= e.code < 500:
            return None
        raise RuntimeError(f"Barsy HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(str(e)[:80])
    # a valid session returns the user object; an API error body (error/err, or no user_id) is
    # NOT a successful login
    if isinstance(out, dict) and out.get("user_id") and not (out.get("error") or out.get("err")):
        return out
    return None


# Barsy role → panel role, keyed on the STABLE role_id (not the localized display name), and
# FAIL-CLOSED: an unrecognised role gets the least-privileged 'packer' (read-only in the panel),
# never write access. Live Barsy roles: 1 Мениджър, 2 Управител, 3 Служител, 4 Оператор.
_ROLE_BY_ID = {1: ("manager", True), 2: ("manager", True), 3: ("staff", False), 4: ("packer", False)}
_MANAGER_NAMES = ("мениджър", "управител", "manager", "администратор", "admin", "owner", "director")


def map_role(profile: dict) -> tuple[str, bool]:
    """Map a Barsy role → (panel_role, is_superuser). Fail-closed on anything unrecognised."""
    rid = profile.get("role_id")
    if rid in _ROLE_BY_ID:
        return _ROLE_BY_ID[rid]
    name = (profile.get("role_name") or "").strip().lower()
    if name in _MANAGER_NAMES:
        return "manager", True
    if name in ("служител", "staff", "employee"):
        return "staff", False
    return "packer", False  # unknown → least privilege (no write access)
