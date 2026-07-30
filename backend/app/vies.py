# -*- coding: utf-8 -*-
"""VIES — EU VAT-number validation, used at ФАКТУРА time (owner-approved 2026-07-30).

An invalid ДДС № caught BEFORE Barsy issues the фактура is a correction; caught afterwards it is a
credit note plus a re-issue, and for an intra-EU sale it can mean the zero-rating was wrong. So the
check belongs where the invoice is issued — here — rather than at checkout.

🚨 THIS MUST NEVER BLOCK. VIES is a federation of member-state registries and its uptime is famously
poor; "the Bulgarian registry is down" and "this VAT number is fake" are completely different facts and
must never collapse into one refusal. Hence THREE states, never a boolean:

    verified      — VIES said isValid:true. Company name/address returned.
    not_verified  — VIES gave an AUTHORITATIVE negative (userError INVALID).
    unchecked     — anything else: timeout, member state unavailable, service busy, network, bad input.
                    The invoice proceeds; staff see "непроверен", not "невалиден".

`unchecked` is deliberately NOT an error state in the UI. Treating an unreachable service as a failed
validation is the same fail-open/fail-closed confusion that produced a day of silent-failure bugs on
2026-07-30 — and on an invoice it would be expensive, because it would stop a legitimate sale.
"""
import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

_BASE = "https://ec.europa.eu/taxation_customs/vies/rest-api"
_TIMEOUT = 8          # VIES is slow; we would rather report `unchecked` than hold an invoice open

# VIES reports WHY it could not answer. Only INVALID is an authoritative "this number is not valid" —
# everything else is the service telling us it could not check, which is our `unchecked`.
_AUTHORITATIVE_NEGATIVE = {"INVALID", "INVALID_INPUT"}

VERIFIED, NOT_VERIFIED, UNCHECKED = "verified", "not_verified", "unchecked"


def split_vat(raw: str) -> tuple[str, str]:
    """'BG 831391124' → ('BG', '831391124'). A bare number is assumed Bulgarian, which is what a
    Дарлинг B2B customer almost always is; anything else must carry its own country prefix."""
    s = re.sub(r"[\s.\-/]", "", str(raw or "")).upper()
    m = re.match(r"^([A-Z]{2})(.+)$", s)
    if m:
        return m.group(1), m.group(2)
    return ("BG", s) if s else ("", "")


def check_vat(raw: str) -> dict:
    """→ {status, vat, country, name, address, reason}. NEVER raises — a validator that can take down
    the invoice queue is worse than no validator."""
    cc, num = split_vat(raw)
    out: dict = {"status": UNCHECKED, "vat": f"{cc}{num}" if cc else "", "country": cc,
                 "name": "", "address": "", "reason": ""}
    # must contain a digit: a bare country code ("BG") otherwise parses as country BG + number "BG"
    # and burns a call on the public service to be told what we could have known locally.
    if (not cc or not num or not re.search(r"\d", num)
            or not re.fullmatch(r"[A-Za-z0-9+*]{2,14}", num)):
        out["reason"] = "непълен или неразпознат формат на ДДС номер"
        return out
    try:
        req = urllib.request.Request(
            f"{_BASE}/ms/{cc}/vat/{num}",
            headers={"Accept": "application/json", "User-Agent": "Darling-Panel/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:   # noqa: S310 — fixed https host
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        # service unreachable/slow/garbled → UNCHECKED, never a negative verdict
        log.info("VIES unreachable for %s%s: %s", cc, num, str(e)[:120])
        out["reason"] = "услугата VIES не отговори"
        return out

    err = str(data.get("userError") or "").upper()
    if data.get("isValid") is True:
        out.update(status=VERIFIED, name=str(data.get("name") or "").strip(),
                   address=" ".join(str(data.get("address") or "").split()))
        return out
    if err in _AUTHORITATIVE_NEGATIVE:
        out.update(status=NOT_VERIFIED, reason="VIES не разпознава този ДДС номер")
        return out
    # MS_UNAVAILABLE, TIMEOUT, SERVICE_UNAVAILABLE, *_MAX_CONCURRENT_REQ … — the registry could not
    # answer. Say so plainly; do NOT let it read as a failed validation.
    out["reason"] = f"регистърът не отговори ({err.lower() or 'без причина'})"
    return out


def member_state_available(cc: str) -> bool | None:
    """Is a member state's registry currently answering? None if we couldn't tell. Used to explain an
    `unchecked` to staff ('BG регистърът е недостъпен') rather than leaving it unexplained."""
    try:
        req = urllib.request.Request(f"{_BASE}/check-status",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:   # noqa: S310 — fixed https host
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:  # noqa: BLE001 — a status probe must never be the thing that breaks a check
        return None
    for row in data.get("countries") or []:
        if str(row.get("countryCode") or "").upper() == cc.upper():
            return str(row.get("availability") or "").lower().startswith("available")
    return None
