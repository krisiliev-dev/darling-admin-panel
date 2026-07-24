# -*- coding: utf-8 -*-
"""Barsy REST API client (owner 2026-07-19) — shared by every Darling agent.

Direct-connection API (docs: https://docs.lukanet.com/barsy.api/). Auth = HTTP Basic.
Every method is POST {BARSY_API_BASE}/{Method} with a JSON body; the JSON response is
returned parsed. Credentials come from the shared secret file ~/.hermes/.env:

    BARSY_HOST=darling7.barsy.online
    BARSY_API_BASE=https://darling7.barsy.online/endpoints/json
    BARSY_USER=Claude
    BARSY_PASS=...

Usage:
    import barsy_api as b
    b.call("Amounttypes_getlist")            # read
    b.call("Articles_create", {"article": {...}})   # write

    python barsy_api.py probe                 # read-only auth + schema introspection
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ENV = Path.home() / ".hermes" / ".env"


def _env():
    """Load BARSY_* from the shared secret file (and process env as override)."""
    vals = {}
    if ENV.exists():
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k.startswith("BARSY_"):
                vals[k.strip()] = v.strip()
    for k in ("BARSY_HOST", "BARSY_API_BASE", "BARSY_USER", "BARSY_PASS"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    base = vals.get("BARSY_API_BASE") or (
        "https://" + vals.get("BARSY_HOST", "") + "/endpoints/json")
    return base.rstrip("/"), vals.get("BARSY_USER", ""), vals.get("BARSY_PASS", "")


class BarsyError(RuntimeError):
    pass


def call(method, body=None, bid=None, timeout=60):
    """POST one Barsy API method. `body` is a dict/list (JSON). Returns parsed JSON.
    `bid` selects a non-default trade object (?bid=). Raises BarsyError on HTTP/API error."""
    base, user, pw = _env()
    if not user or not pw:
        raise BarsyError("BARSY_USER / BARSY_PASS not set in ~/.hermes/.env")
    url = f"{base}/{method}" + (f"?bid={bid}" if bid else "")
    data = json.dumps(body if body is not None else []).encode("utf-8")
    tok = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
    # Retry transient failures — the low-tier Barsy server throws read-timeouts and
    # "сървъра е зает" (busy) blips under load. Retry those a few times with backoff;
    # real HTTP/API errors raise immediately.
    import time as _time
    payload = None
    last = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Basic " + tok)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if ("зает" in detail or e.code in (502, 503, 504)) and attempt < 3:
                last = f"HTTP {e.code}: {detail[:120]}"
                _time.sleep(1.5 * (attempt + 1))
                continue
            raise BarsyError(f"HTTP {e.code} on {method}: {detail}")
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            last = getattr(e, "reason", None) or str(e)
            if attempt < 3:
                _time.sleep(1.5 * (attempt + 1))
                continue
            raise BarsyError(f"connection error on {method} after retries: {last}")
    if payload is None:
        raise BarsyError(f"no response on {method}: {last}")
    try:
        out = json.loads(payload)
    except Exception:
        raise BarsyError(f"non-JSON response on {method}: {payload[:300]}")
    # Barsy signals API-level errors in the body (error/err/message); surface them.
    if isinstance(out, dict) and (out.get("error") or out.get("err")):
        raise BarsyError(f"API error on {method}: {json.dumps(out, ensure_ascii=False)[:400]}")
    return out


def upload_image(obj_id, image_bytes, filename, obj_property="PICTURE",
                 obj_type="articles", timeout=60):
    """Upload a product image to a Barsy object (verified live 2026-07-19).

    Files_upload is the ONE non-JSON endpoint: multipart/form-data with form fields
    obj_type/obj_id/obj_property and the binary as a `files[]` file part (real extension
    required; Barsy resizes + thumbnails it and sets article.picture). Returns the parsed
    file-metadata list. `filename` must end in a valid image extension (.jpg/.png/...)."""
    import uuid
    base, user, pw = _env()
    if not user or not pw:
        raise BarsyError("BARSY_USER / BARSY_PASS not set in ~/.hermes/.env")
    ct = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    boundary = "----barsy" + uuid.uuid4().hex
    pre = b""
    for k, v in [("obj_type", obj_type), ("obj_id", str(obj_id)),
                 ("obj_property", obj_property)]:
        pre += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[]\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ct}\r\n\r\n").encode()
    body = pre + head + image_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/Files_upload", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", "Basic " +
                   base64.b64encode(f"{user}:{pw}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise BarsyError(f"HTTP {e.code} on Files_upload: "
                         f"{e.read().decode('utf-8','replace')[:300]}")
    return out


def probe():
    """Read-only: confirm auth works and capture the real field schema of this install."""
    base, user, pw = _env()
    print(f"base={base}  user={user}  pass={'set('+str(len(pw))+')' if pw else 'MISSING'}")
    for method, body in [
        ("Languages_getlist", []),
        ("Amounttypes_getlist", []),
        ("Categories_getalltree", []),
        ("Articles_getlist", {"length": 2}),
    ]:
        try:
            out = call(method, body)
            s = json.dumps(out, ensure_ascii=False)
            print(f"\n### {method}: OK ({len(s)} chars)")
            print(s[:1200])
        except BarsyError as e:
            print(f"\n### {method}: FAIL — {e}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["probe"]:
        probe()
    else:
        print(__doc__)
