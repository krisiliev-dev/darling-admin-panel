# -*- coding: utf-8 -*-
"""nekorekten.com client — Bulgarian crowdsourced risky-customer (COD-refuser) reports.

Server-to-server: the API key lives in config (env), so it never reaches the browser and the source
IP nekorekten sees is this backend's egress IP (the one to whitelist). We store/return only a COUNT
of reports, never the report content — that keeps us clear of the ToS 'no commercial redistribution'
line and of copying third-party personal data around (GDPR). A hit is a FLAG for human review, never
an auto-reject (GDPR Art. 22).

Free tier is ~30 checks/day + 10 req/min, so callers should check one order at a time, not sweep.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .config import get_settings


def normalize_phone(raw: str) -> str:
    """Canonical BG number (digits, 359-prefixed) so the same phone matches across formats."""
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return ""
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("359"):
        return d
    if d.startswith("0"):
        return "359" + d[1:]
    if len(d) == 9:               # bare national number
        return "359" + d
    return d


def enabled() -> bool:
    return bool(get_settings().nekorekten_api_key)


def search_url(phone: str) -> str:
    return get_settings().nekorekten_search_url.replace("{phone}", urllib.parse.quote(phone or ""))


def check_phone(phone: str) -> dict:
    """→ {enabled, reports:int|None, error?}. reports=None means 'not looked up' (disabled or error).
    Never raises — a lookup failure must not block order handling."""
    s = get_settings()
    ph = normalize_phone(phone)
    if not s.nekorekten_api_key or not ph:
        return {"enabled": bool(s.nekorekten_api_key), "reports": None}
    qs = urllib.parse.urlencode({"phone": ph})
    req = urllib.request.Request(
        f"{s.nekorekten_api_base}/reports?{qs}",
        # a real User-Agent is REQUIRED — nekorekten's WAF 403s the default python-urllib UA
        headers={"Api-Key": s.nekorekten_api_key, "Accept": "application/json",
                 "User-Agent": "Darling-Panel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"enabled": True, "reports": None, "error": f"HTTP {e.code}"}
    except Exception as e:  # network/timeout/parse — degrade gracefully
        return {"enabled": True, "reports": None, "error": str(e)[:80]}
    return {"enabled": True, "reports": _report_count(data)}


def report(text: str, phone: str = "", first_name: str = "", last_name: str = "",
           city_id: int | None = None) -> dict:
    """Submit a report about a non-collecting/refusing customer to nekorekten (POST /reports). This
    PUBLISHES personal data to a third-party public database — call ONLY on explicit human action,
    never automatically, and only with factual grounds. `text` is required. Returns {ok, report_id}
    so we can store the id locally (not personal data) and DELETE the report later if it was a mistake.
    GDPR: lawful basis = fraud prevention (legitimate interest) — must be covered in the privacy policy."""
    s = get_settings()
    if not s.nekorekten_api_key:
        return {"ok": False, "error": "no api key"}
    if not (text or "").strip():
        return {"ok": False, "error": "text required"}
    payload = {"text": text.strip()}
    ph = normalize_phone(phone)
    if ph:
        payload["phone"] = "+" + ph if not ph.startswith("+") else ph
    if first_name:
        payload["firstName"] = first_name.strip()
    if last_name:
        payload["lastName"] = last_name.strip()
    if city_id:
        payload["cityID"] = int(city_id)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{s.nekorekten_api_base}/reports", data=body, method="POST",
        headers={"Api-Key": s.nekorekten_api_key, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "Darling-Panel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}
    rid = None
    if isinstance(data, dict):
        rid = data.get("id") or (data.get("data") or {}).get("id") or data.get("reportId")
    return {"ok": True, "report_id": rid}


def delete_report(report_id: str) -> dict:
    """Retract a report we filed (DELETE /reports/{id}) — for a mistaken/withdrawn report."""
    s = get_settings()
    if not s.nekorekten_api_key or not report_id:
        return {"ok": False, "error": "missing key or id"}
    req = urllib.request.Request(
        f"{s.nekorekten_api_base}/reports/{urllib.parse.quote(str(report_id))}", method="DELETE",
        headers={"Api-Key": s.nekorekten_api_key, "Accept": "application/json",
                 "User-Agent": "Darling-Panel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return {"ok": True}
    except urllib.error.HTTPError as e:
        # 404 = already gone → treat as success (idempotent retract)
        return {"ok": True} if e.code == 404 else {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def _report_count(data) -> int:
    """Reports total from the API response. Real shape: {items:[...], count:N, currentPage,…} —
    `count` is the TOTAL across pages, so prefer it; fall back to list lengths / other shapes."""
    if isinstance(data, dict):
        if isinstance(data.get("count"), int):
            return data["count"]
        if isinstance(data.get("items"), list):
            return len(data["items"])
        if isinstance(data.get("data"), list):
            return len(data["data"])
        meta = data.get("meta") or {}
        try:
            return int(meta.get("total") or data.get("total") or 0)
        except (TypeError, ValueError):
            return 0
    if isinstance(data, list):
        return len(data)
    return 0
