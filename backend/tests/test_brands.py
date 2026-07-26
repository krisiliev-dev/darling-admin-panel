# -*- coding: utf-8 -*-
"""Brands: gazetteer picker (bundled snapshot ∪ panel store) + add-new-brand."""
import pytest


@pytest.mark.asyncio
async def test_search_bundled_gazetteer(owner_client):
    # the bundled snapshot has the 107 confirmed brands (Luminarc among them)
    r = await owner_client.get("/api/brands", params={"q": "lumin"})
    assert r.status_code == 200, r.text
    names = [b["name"] for b in r.json()["brands"]]
    assert any(n.lower() == "luminarc" for n in names)
    # every row carries the full schema (defaults applied to old minimal rows)
    for b in r.json()["brands"]:
        assert set(b) >= {"name", "aliases", "logo", "confirmed", "source"}


@pytest.mark.asyncio
async def test_add_brand_then_findable(owner_client):
    add = await owner_client.post("/api/brands", json={"name": "Дарлинг Хоум", "aliases": ["Darling Home"]})
    assert add.status_code == 201, add.text
    assert add.json()["brand"]["source"] == "panel" and add.json()["brand"]["confirmed"] is False
    # findable by name AND by alias
    by_name = (await owner_client.get("/api/brands", params={"q": "дарлинг"})).json()["brands"]
    assert any(b["name"] == "Дарлинг Хоум" for b in by_name)
    by_alias = (await owner_client.get("/api/brands", params={"q": "darling home"})).json()["brands"]
    assert any(b["name"] == "Дарлинг Хоум" for b in by_alias)
    # idempotent
    again = await owner_client.post("/api/brands", json={"name": "Дарлинг Хоум"})
    assert again.status_code == 201 and again.json()["created"] is False


@pytest.mark.asyncio
async def test_edit_and_delete_panel_brand(owner_client):
    await owner_client.post("/api/brands", json={"name": "Мебелко"})
    # edit: aliases + logo + confirmed
    up = await owner_client.put("/api/brands/Мебелко", json={"aliases": ["Mebelko"], "logo": "http://x/l.png", "confirmed": True})
    assert up.status_code == 200, up.text
    assert up.json()["brand"]["confirmed"] is True and up.json()["brand"]["logo"] == "http://x/l.png"
    assert (await owner_client.get("/api/brands", params={"q": "mebelko"})).json()["brands"]   # alias match
    # editing a BUNDLED brand creates a panel override
    ov = await owner_client.put("/api/brands/Luminarc", json={"aliases": ["Люминарк"], "logo": "", "confirmed": True})
    assert ov.status_code == 200 and ov.json()["brand"]["source"] == "panel"
    # delete the panel brand
    d = await owner_client.delete("/api/brands/Мебелко")
    assert d.status_code == 200
    # a bundled-only brand can't be deleted from the panel store
    d2 = await owner_client.delete("/api/brands/BRIO")
    assert d2.status_code == 404


@pytest.mark.asyncio
async def test_add_brand_requires_staff(packer_client):
    r = await packer_client.post("/api/brands", json={"name": "X"})
    assert r.status_code == 403
