# -*- coding: utf-8 -*-
"""Клиенти (/api/insights) — the behavioural customer cut on the Анализи dashboard. It had NO test:
the whole endpoint is derived arithmetic over Shopify orders + our OrderOutcome rows, and every number
is rendered straight into a <Stat>. Two classes of regression are covered here:

  * the SHAPE — Dashboard.tsx indexes `data.customers.repeat_rate`, `data.delivery.rto_rate`,
    `data.top_cities[].city`… unconditionally (see InsightsResponse in frontend/src/lib/api.ts). A
    renamed or dropped key is a blank/crashing dashboard, not a type error.
  * the DIVISORS — aov, cod_share, repeat_rate and rto_rate all divide by a count that is ZERO on an
    empty store (pre-launch = the normal state today). A missing guard is a 500 on the landing tab.

No real Shopify: we monkeypatch shopify_orders.fetch_orders (the router's only upstream).
"""
from app.db import async_session_maker
from app.models import OrderOutcome
from app.routers import insights

# Two customers (by phone) — 0888 ordered twice, 0999 once — across two days and two cities.
# Totals deliberately mix str and float: the router's _f() must coerce both (Shopify returns strings).
_ORDERS = [
    {"id": "1", "total": "10.00", "cod": True, "phone": "0888", "created": "2026-07-01T10:00:00Z",
     "city": "София", "items": [{"title": "Чаша", "qty": 2}]},
    {"id": "2", "total": 20.0, "cod": False, "phone": "0888", "created": "2026-07-01T12:00:00Z",
     "city": "София", "items": [{"title": "Чаша", "qty": 1}]},
    {"id": "3", "total": 30.0, "cod": True, "phone": "0999", "created": "2026-07-02T09:00:00Z",
     "city": "Пловдив", "items": [{"title": "Купа", "qty": 5}]},
]


def _orders(monkeypatch, rows=None, seen=None):
    """Patch the single upstream. `seen` (a dict) captures the limit the router forwards."""
    def _fetch(limit):
        if seen is not None:
            seen["limit"] = limit
        return list(_ORDERS if rows is None else rows)
    monkeypatch.setattr(insights.shopify_orders, "fetch_orders", _fetch)


async def _outcomes(*outcomes):
    async with async_session_maker() as s:
        for i, o in enumerate(outcomes):
            s.add(OrderOutcome(phone_norm=f"35988800000{i}", outcome=o))
        await s.commit()


# --- the contract the dashboard renders ---------------------------------------------
async def test_response_carries_every_key_the_dashboard_reads(owner_client, monkeypatch):
    """Dashboard.tsx destructures these without optional chaining — a renamed/removed key blanks or
    crashes the Анализи tab, and TypeScript can't catch it (the response is `any` over the wire)."""
    _orders(monkeypatch)
    r = await owner_client.get("/api/insights")
    assert r.status_code == 200, r.text
    b = r.json()
    # "warning" is part of the contract too: non-empty means the numbers are INCOMPLETE (Shopify
    # unreachable) rather than genuinely zero — the dashboard must be able to say so.
    assert set(b) == {"warning", "orders", "revenue", "aov", "cod_share", "customers",
                      "by_day", "top_cities", "top_products", "delivery"}
    assert set(b["customers"]) == {"total", "returning", "repeat_rate"}
    assert set(b["delivery"]) == {"delivered", "refused", "rto_rate"}
    assert set(b["by_day"][0]) == {"date", "orders", "revenue"}
    assert set(b["top_cities"][0]) == {"city", "orders"}
    assert set(b["top_products"][0]) == {"name", "qty"}


async def test_headline_numbers(owner_client, monkeypatch):
    """AOV and 'Дял НП' are owner-facing KPIs; a divisor slip (e.g. dividing by customers instead of
    orders, or forgetting the *100) is silently plausible-looking and would misprice decisions."""
    _orders(monkeypatch)
    b = (await owner_client.get("/api/insights")).json()
    assert b["orders"] == 3
    assert b["revenue"] == 60.0          # "10.00" + 20.0 + 30.0 — the string total must count
    assert b["aov"] == 20.0              # revenue / orders
    assert b["cod_share"] == 66.7        # 2 of 3 COD, one decimal


async def test_new_vs_returning_counts_customers_by_phone(owner_client, monkeypatch):
    """'Клиенти' / 'Повторни' is the whole point of this view. Counting ORDERS instead of distinct
    phones (or counting every phone as returning) would report loyalty we don't have."""
    _orders(monkeypatch)
    c = (await owner_client.get("/api/insights")).json()["customers"]
    assert c["total"] == 2               # two distinct phones, not three orders
    assert c["returning"] == 1           # only 0888 ordered more than once
    assert c["repeat_rate"] == 50.0


async def test_geography_and_product_affinity_are_ranked_biggest_first(owner_client, monkeypatch):
    """These feed two horizontal bar charts; the frontend does NOT sort, so the ranking is the
    router's job. Quantities must SUM per title, not count line items."""
    _orders(monkeypatch)
    b = (await owner_client.get("/api/insights")).json()
    assert b["top_cities"] == [{"city": "София", "orders": 2}, {"city": "Пловдив", "orders": 1}]
    assert b["top_products"][0] == {"name": "Купа", "qty": 5}
    assert b["top_products"][1] == {"name": "Чаша", "qty": 3}      # 2 + 1 summed across two orders


async def test_by_day_is_chronological_and_sums_per_day(owner_client, monkeypatch):
    _orders(monkeypatch)
    assert (await owner_client.get("/api/insights")).json()["by_day"] == [
        {"date": "2026-07-01", "orders": 2, "revenue": 30.0},
        {"date": "2026-07-02", "orders": 1, "revenue": 30.0},
    ]


async def test_days_window_keeps_the_MOST_RECENT_days(owner_client, monkeypatch):
    """The window is a tail slice ([-days:]). A `[:days]` typo still returns the right COUNT of days,
    so it passes a length check while showing the OLDEST days — an invisible, badly wrong chart."""
    _orders(monkeypatch)
    b = (await owner_client.get("/api/insights", params={"days": 1})).json()
    assert [d["date"] for d in b["by_day"]] == ["2026-07-02"]


async def test_delivery_success_comes_from_our_own_outcomes(owner_client, monkeypatch):
    """RTO% drives a red 'blocked' tone above 15% and is the COD-refuser signal. rto_rate must be
    refused / (delivered + refused) — dividing by total orders instead would understate it."""
    _orders(monkeypatch)
    await _outcomes("delivered", "delivered", "refused")
    d = (await owner_client.get("/api/insights")).json()["delivery"]
    assert d == {"delivered": 2, "refused": 1, "rto_rate": 33.3}


# --- empty / hostile upstream: the dashboard must still render ----------------------
async def test_empty_store_returns_zeros_not_a_500(owner_client, monkeypatch):
    """THE pre-launch state (and the one the sticky-note 'Още няма поръчки' UI is written for).
    aov / cod_share / repeat_rate / rto_rate each divide by a count that is 0 here — drop any one of
    those guards and the whole Анализи landing tab 500s."""
    _orders(monkeypatch, rows=[])
    r = await owner_client.get("/api/insights")
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["orders"], b["revenue"], b["aov"], b["cod_share"]) == (0, 0, 0.0, 0.0)
    assert b["customers"] == {"total": 0, "returning": 0, "repeat_rate": 0.0}
    assert b["delivery"] == {"delivered": 0, "refused": 0, "rto_rate": 0.0}
    assert b["by_day"] == [] and b["top_cities"] == [] and b["top_products"] == []


async def test_partial_order_rows_do_not_500(owner_client, monkeypatch):
    """Real Shopify orders arrive with nulls: no shipping address (→ no phone/city), a draft with no
    line items, a null total. Each of those is an attribute the router reaches into, so an unguarded
    read turns one odd order into a dead dashboard for everyone."""
    _orders(monkeypatch, rows=[
        {"id": "x", "total": None, "cod": False, "phone": None, "created": None,
         "city": None, "items": None},
        {"id": "y", "total": "не-число", "cod": True, "created": "", "items": [{"title": None, "qty": None}]},
        {"id": "z"},                       # nothing but an id
    ])
    r = await owner_client.get("/api/insights")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["orders"] == 3 and b["revenue"] == 0.0     # unparseable/missing totals count as 0
    assert b["customers"]["total"] == 0                 # no phone → not a customer
    assert b["by_day"] == [] and b["top_cities"] == [] and b["top_products"] == []


# --- inputs + auth ------------------------------------------------------------------
async def test_limit_is_forwarded_to_shopify_and_bounded(owner_client, monkeypatch):
    """`limit` becomes the page size of a real Shopify GraphQL call. Losing the forward silently
    caps every KPI at the default; losing the ge/le bounds lets a URL trigger an unbounded pull."""
    seen: dict = {}
    _orders(monkeypatch, seen=seen)
    assert (await owner_client.get("/api/insights", params={"limit": 42})).status_code == 200
    assert seen["limit"] == 42
    assert (await owner_client.get("/api/insights", params={"limit": 5000})).status_code == 422
    assert (await owner_client.get("/api/insights", params={"days": 0})).status_code == 422


async def test_requires_auth(client, monkeypatch):
    _orders(monkeypatch)
    assert (await client.get("/api/insights")).status_code == 401


async def test_readable_by_the_lowest_role(packer_client, monkeypatch):
    # a read-only dashboard view — everyone logged in sees it (mirrors /api/dashboard/catalog)
    _orders(monkeypatch)
    assert (await packer_client.get("/api/insights")).status_code == 200


async def test_shopify_outage_degrades_instead_of_500ing_the_dashboard(owner_client, monkeypatch):
    """Анализи is the landing tab. A Shopify credential/network failure must return zeros + a warning
    (the same way the order queue does), not a 500 that blanks the whole dashboard."""
    def boom(limit=250):
        raise RuntimeError("shopify 503")
    monkeypatch.setattr(insights.shopify_orders, "fetch_orders", boom)
    r = await owner_client.get("/api/insights")
    assert r.status_code == 200, r.text
    b = r.json()
    assert "Shopify недостъпен" in b["warning"]      # the numbers are incomplete, not truly zero
    assert b["orders"] == 0 and b["revenue"] == 0
