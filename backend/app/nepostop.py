# -*- coding: utf-8 -*-
"""nepostop.com — the SECOND COD-refuser registry (owner-approved 2026-07-30), alongside nekorekten.

COD is 55–60% of Дарлинг's orders, so a customer who habitually refuses deliveries costs real money in
courier fees and re-stocking. Two independent registries cover more of them than one.

🚨 ADVISORY ONLY, NEVER BLOCKING, NEVER AT CHECKOUT. Refusing service because a third party's list says
so is automated decision-making about a person, and "a competitor reported this phone number" is a poor
basis for it. The panel FLAGS an order for a human in Онлайн поръчки — "this number appears on a refuser
list, consider requiring prepayment" — and a person decides. That also fits the review-first order flow.

🚨 FAIL OPEN. If nepostop is down, slow, rate-limited or garbled, the order proceeds UNFLAGGED. A risk
lookup that blocks orders when the *lookup* fails is worse than no lookup at all.

🚨 RECORD THE SOURCE AND THE TIME, NOT A MERGED SCORE. If a customer disputes being flagged, "flagged"
is unanswerable; "nepostop, checked 3 Aug, 7 of 9 deliveries refused" is something a human can stand
behind — and under GDPR the person has a right to know the basis of a decision about them.

Contributing our own outcomes back (`report_shipment`) is what keeps a shared registry useful, and the
owner opted in — but it is gated SEPARATELY (`nepostop_report_live`, default off), because sending our
customers' data outbound is a different decision from querying, and it should be switched on
deliberately rather than inherited from the lookup being enabled.
"""
import json
import logging
import urllib.error
import urllib.request

from .config import get_settings
from .nekorekten import normalize_phone

log = logging.getLogger(__name__)
_TIMEOUT = 6          # a risk hint is never worth holding an order page open for


def _national(phone: str) -> str:
    """nepostop's API documents the NATIONAL form ("0890123456"), while our shared normaliser emits
    international ("359890123456") because that is what nekorekten wants. Sending the wrong shape would
    not error — it would just return found:false for everybody, i.e. an integration that looks healthy
    and never finds anyone. So normalise per provider rather than sharing one format."""
    ph = normalize_phone(phone)
    if ph.startswith("359") and len(ph) > 3:
        return "0" + ph[3:]
    return ph


def enabled() -> bool:
    return bool(get_settings().nepostop_api_key)


def _post(path: str, payload: dict) -> tuple[dict | None, str]:
    """→ (data, error). Never raises: every failure becomes an error string the caller reports as
    'not looked up', which is materially different from 'looked up, found nothing'."""
    s = get_settings()
    if not s.nepostop_api_key:
        return None, "no_key"
    req = urllib.request.Request(
        f"{s.nepostop_api_base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-API-Key": s.nepostop_api_key, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "Darling-Panel/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:   # noqa: S310 — fixed https host
            return json.loads(r.read().decode("utf-8", "replace") or "{}"), ""
    except urllib.error.HTTPError as e:
        # 429 is their documented rate limit (60/min, 1000/h). Distinguish it so a staff member reading
        # "не е проверен" knows it was our pacing, not the customer.
        return None, "rate_limited" if e.code == 429 else f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return None, str(e)[:80]


def check_phone(phone: str) -> dict:
    """→ {enabled, checked, found, total, refused, refused_pct, error?}.

    `checked=False` means WE DID NOT LOOK — no key, bad number, service down. It must never be rendered
    as 'clean': an unchecked customer and a customer with a clean record are different facts, and
    collapsing them is how a risk tool quietly stops doing anything."""
    s = get_settings()
    ph = _national(phone)
    out: dict = {"enabled": bool(s.nepostop_api_key), "checked": False, "found": False,
                 "total": None, "refused": None, "refused_pct": None}
    if not s.nepostop_api_key or not ph:
        if not ph and s.nepostop_api_key:
            out["error"] = "невалиден телефон"
        return out
    data, err = _post("/phone-check", {"phone_number": ph})
    if data is None:
        out["error"] = err
        return out
    _st = data.get("stats")
    st: dict = _st if isinstance(_st, dict) else {}
    out.update(
        checked=True,
        found=bool(data.get("found")),
        total=_int(st.get("total")),
        refused=_int(st.get("undelivered")),
        refused_pct=_num(st.get("undelivered_percentage")),
    )
    return out


def report_shipment(phone: str, waybill: str) -> dict:
    """Contribute one shipment to the registry. Gated on `nepostop_report_live` INDEPENDENTLY of
    lookups — this sends a customer's phone number outward, which is a disclosure, not a query.
    Off → returns what it WOULD have sent, so the flow can be exercised without disclosing anything."""
    s = get_settings()
    ph = _national(phone)
    body = {"phone_number": ph, "shipping_number": str(waybill or "").strip()}
    if not ph or not body["shipping_number"]:
        return {"ok": False, "sent": False, "error": "липсва телефон или товарителница"}
    if not s.nepostop_api_key:
        return {"ok": False, "sent": False, "error": "no_key", "body": body}
    if not s.nepostop_report_live:
        return {"ok": True, "sent": False, "dry_run": True, "body": body}
    data, err = _post("/shipments", body)
    if data is None:
        # a failed contribution is OUR problem, not the customer's — log it and move on; nothing about
        # the order should change because a registry upload failed.
        log.info("nepostop report failed for waybill %s: %s", body["shipping_number"], err)
        return {"ok": False, "sent": False, "error": err, "body": body}
    return {"ok": True, "sent": True, "result": data}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None
