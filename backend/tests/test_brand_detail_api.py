# -*- coding: utf-8 -*-
"""Brand DETAIL endpoints (brand editor rework, 2026-08-01). Properties under test:
the detail view merges overrides and LABELS them; the new brand-scope fields (confirmed, kind)
round-trip through an override; an unknown brand is refused, not invented; and the products list
either answers truthfully or fails loudly — never an empty list standing in for a missing snapshot."""


async def _first_brand(client) -> str:
    return (await client.get("/api/brand-registry/brands")).json()["brands"][0]["brand_id"]


async def test_detail_is_readable_and_carries_prefixes(staff_client):
    bid = await _first_brand(staff_client)
    r = await staff_client.get(f"/api/brand-registry/brand/{bid}")
    assert r.status_code == 200
    d = r.json()
    assert d["brand_id"] == bid
    assert isinstance(d["prefixes"], list) and isinstance(d["manufacturers"], list)
    assert d["overridden"] == []                       # machine values, labelled as such
    for row in d["prefixes"]:
        assert set(row) >= {"prefix", "asserts", "product_count", "overridden"}


async def test_unknown_brand_is_refused(owner_client):
    r = await owner_client.get("/api/brand-registry/brand/no-such-brand")
    assert r.status_code == 404
    r = await owner_client.post("/api/brand-registry/override", json={
        "scope": "brand", "ref": "no-such-brand", "confirmed": True, "why": "тест"})
    assert r.status_code == 404                        # a target that doesn't exist is refused


async def test_confirmed_and_kind_override_round_trip_and_are_labelled(owner_client):
    bid = await _first_brand(owner_client)
    r = await owner_client.post("/api/brand-registry/override", json={
        "scope": "brand", "ref": bid, "confirmed": True, "kind": "supplier_label",
        "why": "тест на детайлния редактор"})
    assert r.status_code == 200, r.text
    d = (await owner_client.get(f"/api/brand-registry/brand/{bid}")).json()
    assert d["confirmed"] is True and d["kind"] == "supplier_label"
    assert "confirmed" in d["overridden"] and "kind" in d["overridden"]
    assert d["overridden_by"]                          # attributable
    # cleanup: fall back to the machine values
    x = await owner_client.delete(f"/api/brand-registry/override?scope=brand&ref={bid}")
    assert x.status_code == 200
    d2 = (await owner_client.get(f"/api/brand-registry/brand/{bid}")).json()
    assert d2["overridden"] == []


async def test_products_list_answers_or_fails_loudly(staff_client):
    """200 with the truthful shape when the snapshot ships; 503 when it is absent. NEVER a bare
    empty 200 caused by a missing file — that would claim 'no products' about a brand we simply
    cannot see."""
    bid = await _first_brand(staff_client)
    r = await staff_client.get(f"/api/brand-registry/brand/{bid}/products?limit=5")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        d = r.json()
        assert set(d) >= {"total", "returned", "items", "asserting_prefixes"}
        assert d["returned"] <= 5
        for it in d["items"]:
            assert set(it) >= {"code", "name", "enriched"}
    else:
        assert "code_prefixes.json" in r.json()["detail"]


async def test_products_of_unknown_brand_are_refused(staff_client):
    r = await staff_client.get("/api/brand-registry/brand/no-such-brand/products")
    assert r.status_code == 404


async def test_prefix_products_answers_or_fails_loudly(staff_client):
    """Same truth-or-503 property as the brand list, for the review queue's expandable rows."""
    q = (await staff_client.get("/api/brand-registry/review?limit=1")).json()["queue"]
    pref = q[0]["prefix"]
    r = await staff_client.get(f"/api/brand-registry/prefix/{pref}/products?limit=4")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        d = r.json()
        assert set(d) >= {"prefix", "total", "returned", "items"}
        assert d["returned"] <= 4
        for it in d["items"]:
            assert set(it) >= {"code", "name", "photo", "enriched"}
    else:
        assert "code_prefixes.json" in r.json()["detail"]


async def test_prefix_products_unknown_prefix_is_refused(staff_client):
    r = await staff_client.get("/api/brand-registry/prefix/000000000/products")
    assert r.status_code == 404
