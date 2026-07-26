# -*- coding: utf-8 -*-
"""OPT-IN live-integration smoke suite for the Дарлинг admin-panel backend.

These tests hit the REAL external integrations (Barsy ERP, Speedy, Econt, nekorekten.com) to
prove they still work. They are SKIPPED by default so the normal `pytest` run stays hermetic
and fast, and they are STRICTLY READ-ONLY / side-effect-free — no writes, no real shipments,
no reports, no report deletions. That constraint is load-bearing: this touches real money/data.

Run them explicitly:

    # from admin-panel/backend
    export PYTHONIOENCODING=utf-8
    RUN_INTEGRATION=1 python -m pytest -q tests/test_integration_live.py

Without RUN_INTEGRATION=1 every test in this module is skipped (module-level `pytestmark`), so
`python -m pytest -q` and CI are unaffected. Gating lives ENTIRELY in this file — the shared
conftest.py is untouched — so no other test is disturbed.

Read-only calls used (and the write-calls deliberately AVOIDED):
  * Barsy   : Articles_getlist / Store_amounts / Suppliers_getlist / Articles_get   (never
              Articles_update / Storeloads_create / any *_save / *_delete).
  * Couriers: Courier.ping() + Courier.calculate()  (never create_shipment / do_write=True).
  * nekorekten: check_phone() only  (never report() / delete_report() — those PUBLISH data).
"""
import os

import pytest

# --- the ONE gate: nothing here runs unless the operator opts in ----------------------
# `integration` marks intent (and enables `-m integration`); `skipif` does the real gating.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="live integration suite; set RUN_INTEGRATION=1 to run (hits real Barsy/couriers/nekorekten)",
    ),
]

# Import the REAL core (no monkeypatching here — that is the whole point). `app.core`
# re-exports the battle-tested darling_core modules that talk to the live services.
from app import nekorekten  # noqa: E402
from app.core import barsy, courier  # noqa: E402


# ======================================================================================
# Helpers
# ======================================================================================
def _barsy(method, body):
    """Call Barsy but stay tolerant of the low-tier server's transient throttling.

    `barsy.call` already retries "сървъра е зает" / 502-504 / timeouts internally; if one still
    surfaces here it is a transient blip, NOT a broken integration — xfail rather than red the
    whole suite. A genuine API/HTTP error (bad method, auth, etc.) is re-raised and fails loudly.
    """
    try:
        return barsy.call(method, body)
    except barsy.BarsyError as e:  # type: ignore[attr-defined]
        msg = str(e).lower()
        transient = any(t in msg for t in ("зает", "busy", "timeout", "connection", "502", "503", "504"))
        if transient:
            pytest.xfail(f"Barsy transient throttle/timeout on {method}: {str(e)[:160]}")
        raise


def _store_amount_rows(resp):
    """Store_amounts returns a Barsy 'page' structure; the actual stock rows live under
    content[*].data.rowList. Dig them out tolerantly (empty list if the shape shifts)."""
    if isinstance(resp, dict):
        for block in resp.get("content", []) or []:
            data = (block or {}).get("data") or {}
            rows = data.get("rowList")
            if isinstance(rows, list):
                return rows
    return []


# ======================================================================================
# Barsy ERP  (read-only)
# ======================================================================================
def test_barsy_articles_getlist_returns_rows():
    rows = _barsy("Articles_getlist", {"filters": {}, "length": 3})
    assert isinstance(rows, list), f"expected a list of articles, got {type(rows).__name__}"
    assert rows, "Articles_getlist returned no rows"
    assert any("article_id" in r for r in rows if isinstance(r, dict)), "rows lack article_id"


def test_barsy_store_amounts_returns_rows():
    resp = _barsy("Store_amounts", {"filters": {}, "length": 3})
    assert resp, "Store_amounts returned an empty response"
    rows = _store_amount_rows(resp)
    assert rows, f"Store_amounts yielded no stock rows (shape: {type(resp).__name__})"


def test_barsy_suppliers_getlist_returns_suppliers():
    rows = _barsy("Suppliers_getlist", {"filters": {}, "length": 3})
    assert isinstance(rows, list), f"expected a list of suppliers, got {type(rows).__name__}"
    assert rows, "Suppliers_getlist returned no suppliers"
    assert any("supplier_id" in r for r in rows if isinstance(r, dict)), "rows lack supplier_id"


def test_barsy_article_get_weight_extra_data():
    """The weight-in-Barsy case: article 450 carries weight_kg in its extra_data.
    Confirms both the Articles_get extra_properties round-trip AND the stored weight value."""
    art = _barsy("Articles_get", {"article_id": 450, "extra_properties": {"extra_data": "1"}})
    assert isinstance(art, dict), f"expected an article dict, got {type(art).__name__}"
    assert art.get("article_id") == 450, f"got the wrong article: {art.get('article_id')!r}"
    extra = art.get("extra_data")
    assert isinstance(extra, dict), f"no extra_data dict on article 450 (got {type(extra).__name__})"
    assert extra.get("weight_kg") == "12.5", f"weight_kg mismatch: {extra.get('weight_kg')!r}"


# ======================================================================================
# Couriers  (side-effect-free ONLY: ping + calculate; NEVER create_shipment)
# ======================================================================================
def _door_shipment():
    """A minimal prepaid (cod=0) door shipment to a well-known Sofia address — enough for
    both adapters to resolve a destination and quote a price. Prepaid + calculate-only means
    zero side effects."""
    return courier.Shipment(
        order_ref="SMOKE-TEST-RO",
        recipient="Иван Тестов",
        phone="0888123456",
        address={"city": "София", "postcode": "1000", "street": "Витоша", "street_no": "1"},
        weight_kg=1.0,
        cod_amount=0.0,
        contents="стоки за дома",
    )


@pytest.mark.parametrize("name", ["speedy", "econt"])
def test_courier_ping(name):
    c = courier.get(name)
    if not c.configured():
        pytest.skip(f"{name} not configured (no credentials in ~/.hermes/.env)")
    res = c.ping()  # auth check + sender identity; no shipment created
    assert isinstance(res, dict) and res.get("ok") is True, f"{name} ping failed: {res!r}"
    assert res.get("sender_name"), f"{name} ping returned no sender identity: {res!r}"


@pytest.mark.parametrize("name", ["speedy", "econt"])
def test_courier_calculate_returns_price(name):
    c = courier.get(name)
    if not c.configured():
        pytest.skip(f"{name} not configured (no credentials in ~/.hermes/.env)")
    quote = c.calculate(_door_shipment())  # /calculate is a cost-free quote, no side effects
    assert isinstance(quote, dict), f"{name} calculate returned {type(quote).__name__}"
    price = quote.get("price")
    assert isinstance(price, (int, float)) and price > 0, f"{name} returned no positive price: {quote!r}"
    assert quote.get("currency"), f"{name} quote missing currency: {quote!r}"


# ======================================================================================
# nekorekten.com  (read-only; consumes the daily free-tier quota — ONE lookup only)
# ======================================================================================
def test_nekorekten_check_phone_returns_count():
    if not nekorekten.enabled():
        pytest.skip("nekorekten not configured (no API key)")
    # Single lookup — the free tier is ~30 checks/day, so we deliberately do exactly one.
    res = nekorekten.check_phone("0888123456")
    assert isinstance(res, dict), f"expected a result dict, got {type(res).__name__}"
    assert res.get("enabled") is True, f"nekorekten reported disabled: {res!r}"
    reports = res.get("reports")
    if reports is None:
        # A degraded lookup (network/throttle/WAF) yields reports=None + an error; don't hard-fail.
        pytest.xfail(f"nekorekten lookup degraded (no count): {res.get('error')!r}")
    assert isinstance(reports, int) and reports >= 0, f"reports is not a count: {reports!r}"
