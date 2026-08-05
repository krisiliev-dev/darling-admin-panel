# -*- coding: utf-8 -*-
"""Newsletter subscriptions — LISTMONK HYBRID (owner decision 2026-08-05).

The storefront newsletter band POSTs here (SERVER-SIDE from WP — the shared X-Office-Token
must never reach a browser); we validate, rate-limit, and create the subscriber in listmonk
(own compose stack on this box, list „Дарлинг бюлетин" = DOUBLE OPT-IN), which then emails
the Bulgarian confirmation link. The subscriber is NOT subscribed until they click — GDPR
consent is the click, not the form submit.

Design rules:
- NO ENUMERATION: an already-subscribed email gets the same 200 {"status":"pending"} as a
  new one — the storefront must not be usable to test who is on the list.
- Fail open on advice: if listmonk is down we return 503 and the storefront shows „опитайте
  по-късно" — never silently drop, never fake a success.
- Credentials (LISTMONK_API_USER/TOKEN, generated box-side) live in ~/.hermes/.env like the
  courier keys; the panel reads them through the same _cred() path. Missing creds = 503
  (newsletter not yet enabled), not a crash.
"""
from __future__ import annotations

import hmac
import json
import re
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..core import courier as courier_mod
from ..issues import record_issue

router = APIRouter(tags=["newsletter"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_PER_IP_PER_HOUR = 20
_GLOBAL_PER_HOUR = 300
_hits: dict[str, list[float]] = {}


class SubscribeIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    source: str = Field(default="footer", max_length=40)
    consent: bool = True
    consent_ts: str | None = Field(default=None, max_length=40)


def _check_token(token: str | None) -> None:
    configured = (get_settings().office_lookup_token or "").strip()
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Newsletter is disabled (no token set).")
    if not token or not hmac.compare_digest(token.strip(), configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing office-lookup token.")


def _rate_ok(ip: str) -> bool:
    now = time.time()
    horizon = now - 3600.0
    allb = _hits.setdefault("*", [])
    dq = _hits.setdefault(ip, [])
    allb[:] = [t for t in allb if t > horizon]
    dq[:] = [t for t in dq if t > horizon]
    if len(allb) >= _GLOBAL_PER_HOUR or len(dq) >= _PER_IP_PER_HOUR:
        return False
    allb.append(now)
    dq.append(now)
    if len(_hits) > 2000:                       # bound the map itself
        _hits.clear()
    return True


def _listmonk_cfg() -> tuple[str, str, str, int]:
    url = (courier_mod._cred("LISTMONK_URL") or "http://darling-listmonk:9000").rstrip("/")
    user = courier_mod._cred("LISTMONK_API_USER")
    tok = courier_mod._cred("LISTMONK_API_TOKEN")
    lid = courier_mod._cred("LISTMONK_LIST_ID")
    if not (user and tok and lid):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Бюлетинът още не е активен (липсва listmonk credential).")
    return url, user, tok, int(lid)


def _listmonk_subscribe(email: str, source: str) -> str:
    """Create the subscriber (unconfirmed) in the double-opt-in list. Returns "pending".
    An existing subscriber is also "pending" — no enumeration. Runs in a threadpool."""
    url, user, tok, lid = _listmonk_cfg()
    body = {"email": email, "name": "", "status": "enabled", "lists": [lid],
            "attribs": {"source": source}, "preconfirm_subscriptions": False}
    req = urllib.request.Request(
        url + "/api/subscribers", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"token {user}:{tok}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return "pending"
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 409 or "already exists" in raw.lower():
            return "pending"                     # duplicate — indistinguishable by design
        raise RuntimeError(f"listmonk HTTP {e.code}: {raw[:160]}")


@router.post("/api/public/newsletter/subscribe")
async def subscribe(
    payload: SubscribeIn,
    request: Request,
    x_office_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_office_token)
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    if not _rate_ok(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Твърде много заявки — опитайте по-късно.")
    email = payload.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Невалиден имейл адрес.")
    if not payload.consent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Липсва съгласие за абонамент.")
    try:
        status_txt = await run_in_threadpool(_listmonk_subscribe, email, payload.source)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — infra failure must be honest, never a fake success
        await record_issue(source="panel", topic="newsletter", order_ref="subscribe",
                           severity="warning", kind="listmonk_subscribe_failed",
                           summary="Абонамент за бюлетина не стигна до listmonk.",
                           detail={"error": str(exc)[:300]})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Бюлетинът е временно недостъпен — опитайте по-късно.") from exc
    return {"status": status_txt}
