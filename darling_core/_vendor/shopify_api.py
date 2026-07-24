# -*- coding: utf-8 -*-
"""Shopify Admin API client (owner 2026-07-19) — shared by the Barsy→Shopify sync.

Auth = client-credentials grant (the "Claude integration" app, all admin scopes). Tokens are
short-lived (~24h) so we cache one to disk with its expiry and refresh on demand. Calls go to
the GraphQL Admin API. Creds come from ~/.hermes/.env:

    SHOPIFY_STORE=r9et0w-1u.myshopify.com
    SHOPIFY_API_VERSION=2026-04
    SHOPIFY_CLIENT_ID=...
    SHOPIFY_CLIENT_SECRET=...

Usage:
    import shopify_api as s
    s.gql("{ shop { name } }")
    s.gql("mutation($p:ProductSetInput!){ productSet(...){...} }", {"p": {...}})
"""
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

ENV = Path.home() / ".hermes" / ".env"
TOKEN_CACHE = Path(__file__).parent / ".shopify_token.json"
UA = "darling-pipeline/1.0"


class ShopifyError(RuntimeError):
    pass


def _env():
    vals = {}
    if ENV.exists():
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln and ln.startswith("SHOPIFY_"):
                k, v = ln.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


def _token():
    """Cached client-credentials access token; refresh when missing/near-expiry."""
    if TOKEN_CACHE.exists():
        try:
            c = json.loads(TOKEN_CACHE.read_text())
            if c.get("expires_at", 0) - time.time() > 300:
                return c["access_token"]
        except Exception:
            pass
    e = _env()
    store = e["SHOPIFY_STORE"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": e["SHOPIFY_CLIENT_ID"],
        "client_secret": e["SHOPIFY_CLIENT_SECRET"]}).encode()
    req = urllib.request.Request(f"https://{store}/admin/oauth/access_token",
                                 data=data, method="POST", headers={"User-Agent": UA})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as ex:
        raise ShopifyError(f"token grant failed: HTTP {ex.code} {ex.read().decode('utf-8','replace')[:200]}")
    at = out.get("access_token")
    if not at:
        raise ShopifyError(f"no access_token in grant response: {out}")
    TOKEN_CACHE.write_text(json.dumps(
        {"access_token": at, "expires_at": time.time() + int(out.get("expires_in", 3600))}))
    return at


def gql(query, variables=None, timeout=60, _retry=True):
    """Run a GraphQL query/mutation. Returns the `data` dict. Raises on transport errors,
    top-level `errors`, and (best-effort) any `userErrors` in the first mutation field."""
    e = _env()
    url = f"https://{e['SHOPIFY_STORE']}/admin/api/{e.get('SHOPIFY_API_VERSION','2026-04')}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "X-Shopify-Access-Token": _token(),
        "Content-Type": "application/json", "User-Agent": UA})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:300]
        if ex.code == 401 and _retry:                      # token expired mid-use → refresh once
            TOKEN_CACHE.unlink(missing_ok=True)
            return gql(query, variables, timeout, _retry=False)
        raise ShopifyError(f"HTTP {ex.code}: {detail}")
    except (TimeoutError, urllib.error.URLError, OSError) as ex:
        raise ShopifyError(f"connection error: {ex}")
    if resp.get("errors"):
        raise ShopifyError(f"GraphQL errors: {json.dumps(resp['errors'], ensure_ascii=False)[:400]}")
    data = resp.get("data") or {}
    for field, val in data.items():
        if isinstance(val, dict) and val.get("userErrors"):
            raise ShopifyError(f"{field} userErrors: {json.dumps(val['userErrors'], ensure_ascii=False)[:400]}")
    return data


if __name__ == "__main__":
    print(json.dumps(gql("{ shop { name currencyCode } productsCount { count } }"),
                     ensure_ascii=False))
