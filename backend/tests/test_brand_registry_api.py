# -*- coding: utf-8 -*-
"""Brand-registry endpoints (#99). The properties under test are the ones the design exists to keep:
overrides WIN and survive regeneration, a suggestion is never presented as a brand, an edit states its
blast radius first, and a correction is attributable."""


async def test_summary_and_review_are_readable_by_any_staff(staff_client):
    r = await staff_client.get("/api/brand-registry/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["asserting_prefixes"] < s["prefixes"]
    q = await staff_client.get("/api/brand-registry/review?limit=5")
    assert q.status_code == 200 and len(q.json()["queue"]) <= 5


async def test_editing_is_owner_only(staff_client):
    """One asserts flip can re-brand thousands of products — a business decision about how the
    catalogue reads, not a system-administration one."""
    r = await staff_client.post("/api/brand-registry/override", json={
        "scope": "brand", "ref": "luminarc", "canonical": "X", "why": "тест"})
    assert r.status_code in (401, 403)


async def test_blast_radius_is_available_BEFORE_the_edit(owner_client):
    """The number belongs in front of the person clicking, not in a report afterwards."""
    q = (await owner_client.get("/api/brand-registry/review?limit=1")).json()["queue"]
    pref = q[0]["prefix"]
    r = await owner_client.get(f"/api/brand-registry/blast-radius?scope=prefix&ref={pref}")
    assert r.status_code == 200 and "product_count" in r.json()


async def test_override_wins_and_is_attributable(owner_client):
    q = (await owner_client.get("/api/brand-registry/review?limit=1")).json()["queue"]
    pref = q[0]["prefix"]
    r = await owner_client.post("/api/brand-registry/override", json={
        "scope": "prefix", "ref": pref, "asserts": True,
        "why": "проверено ръчно от собственика"})
    assert r.status_code == 200, r.text
    row = next(x for x in (await owner_client.get("/api/brand-registry/review?limit=500")
                           ).json()["queue"] if x["prefix"] == pref)
    assert row["asserts"] is True                      # the human value, not the registry's
    assert "asserts" in row["overridden"]              # …and it is LABELLED as human-set
    assert row["overridden_why"] == "проверено ръчно от собственика"
    assert row["overridden_by"]                        # attributable


async def test_a_reason_is_required(owner_client):
    """An unattributable re-brand is not reviewable later, so 'why' is not optional."""
    r = await owner_client.post("/api/brand-registry/override",
                                json={"scope": "brand", "ref": "luminarc", "canonical": "X"})
    assert r.status_code == 422


async def test_unknown_prefix_is_refused_rather_than_stored(owner_client):
    """An override against a target that does not exist would sit forever, silently applying to
    nothing — indistinguishable from a correction that worked."""
    r = await owner_client.post("/api/brand-registry/override", json={
        "scope": "prefix", "ref": "000000000000", "asserts": True, "why": "тест"})
    assert r.status_code == 404


async def test_clearing_falls_back_to_the_machine_value(owner_client):
    q = (await owner_client.get("/api/brand-registry/review?limit=1")).json()["queue"]
    pref = q[0]["prefix"]
    await owner_client.post("/api/brand-registry/override", json={
        "scope": "prefix", "ref": pref, "asserts": True, "why": "тест"})
    d = await owner_client.delete(f"/api/brand-registry/override?scope=prefix&ref={pref}")
    assert d.status_code == 200
    row = next(x for x in (await owner_client.get("/api/brand-registry/review?limit=500")
                           ).json()["queue"] if x["prefix"] == pref)
    assert row["overridden"] == [] and row["asserts"] is False   # back to the registry's value
