# -*- coding: utf-8 -*-
"""Category tree editor (#85c): slim tree proxy + create/rename/move with the verified
Categories_create / Categories_update contracts, slug immutability, guards, roles, gate.
All contracts were live-verified on a throwaway category (probe doc CATEGORY_TREE_85C_PROBE)."""
import pytest

from app.routers import catalog as catalog_module


# REAL Barsy envelope: every node is {"category": {...fields...}, "categories": [...]}.
# This fixture used to be FLAT, which is why a slimming function that produced 14,606 empty
# nodes against the live payload passed this suite. Measured on the box 2026-08-07.
def _n(cat_id, parent_id, name, slug, cnt, children=(), picture=""):
    return {"category": {"cat_id": cat_id, "parent_id": parent_id, "cat_name": name,
                         "slug": slug, "child_articles_cnt": cnt, "cat_picture": picture},
            "categories": list(children)}


TREE = {"category": {"cat_id": None, "parent_id": None, "cat_name": "\u041e\u0441\u043d\u043e\u0432\u043d\u0430",
                     "slug": None, "child_articles_cnt": 43},
        "categories": [
            _n(100, None, "\u0414\u043e\u043c", "hg", 0, [
                _n(110, 100, "\u041a\u0443\u0445\u043d\u044f", "hg-11", 2),
                _n(120, 100, "\u041f\u0440\u043e\u043c\u043e", "hg-x1", 0),
            ]),
        ]}


@pytest.fixture(autouse=True)
def _fresh_tree_cache():
    catalog_module._TREE_CACHE.update({"t": 0.0, "root": None})


def _wire(barsy_recorder):
    barsy_recorder.set_return("Categories_getTree", TREE)
    barsy_recorder.set_return("Categories_get", lambda b: {
        100: {"cat_id": 100, "cat_name": "Дом", "slug": "hg"},
        110: {"cat_id": 110, "cat_name": "Кухня", "slug": "hg-11",
              "cat_name_ml": {"bg_BG": "Кухня", "en_US": "Kitchen"},
              "cat_name_public_ml": {"bg_BG": "Кухня"}, "description_ml": None,
              "parent_id": 100},
        555: {"cat_id": 555, "cat_name": "Ново", "slug": "hg-x2"},
    }.get((b or {}).get("cat_id")))
    barsy_recorder.set_return("Categories_create", 555)
    barsy_recorder.set_return("Categories_update", 1)
    return barsy_recorder


@pytest.mark.asyncio
async def test_tree_is_slim_and_cached(owner_client, barsy_recorder):
    _wire(barsy_recorder)
    r = (await owner_client.get("/api/catalog/categories/tree")).json()["tree"]
    dom = r["children"][0]
    assert set(dom.keys()) == {"cat_id", "parent_id", "name", "slug",
                               "child_articles_cnt", "cat_picture", "children"}
    assert dom["slug"] == "hg" and dom["children"][0]["child_articles_cnt"] == 2
    await owner_client.get("/api/catalog/categories/tree")
    assert barsy_recorder.operations.count("Categories_getTree") == 1   # second hit = cache


@pytest.mark.asyncio
async def test_create_mints_next_x_slug(owner_client, barsy_recorder):
    """hg already has hg-x1 → the new sibling must mint hg-x2, never an official-looking ordinal
    (Shopify ships hundreds of new categories per release — self-minted ordinals WILL collide)."""
    _wire(barsy_recorder)
    r = (await owner_client.post("/api/catalog/categories",
                                 json={"parent_id": 100, "name": "Ново"})).json()
    assert r["cat_id"] == 555 and r["slug"] == "hg-x2"
    cat = barsy_recorder.body_for("Categories_create")["cat"]
    assert cat["slug"] == "hg-x2" and cat["parent_id"] == 100
    assert cat["cat_name_ml"] == {"bg_BG": "Ново"}


@pytest.mark.asyncio
async def test_update_never_sends_slug_and_merges_ml(owner_client, barsy_recorder):
    """Slug = the Shopify taxonomy id — the ONE field a rename must never touch (it resolves the
    attribute schema and the Woo mapping). And a partial {bg_BG} ml map would DROP en_US."""
    _wire(barsy_recorder)
    r = (await owner_client.patch("/api/catalog/categories/110", json={"name": "Кухня 2"})).json()
    assert r["ok"] is True
    body = barsy_recorder.body_for("Categories_update")
    assert body["cat_id"] == 110                       # id at TOP level (verified contract)
    assert "slug" not in body["cat"]
    assert body["cat"]["cat_name_ml"]["en_US"] == "Kitchen"   # merge kept the other locale
    assert body["cat"]["cat_name_ml"]["bg_BG"] == "Кухня 2"


@pytest.mark.asyncio
async def test_move_sends_parent_id(owner_client, barsy_recorder):
    _wire(barsy_recorder)
    r = (await owner_client.patch("/api/catalog/categories/110", json={"parent_id": 120})).json()
    assert r["ok"] is True
    assert barsy_recorder.body_for("Categories_update")["cat"] == {"parent_id": 120}


@pytest.mark.asyncio
async def test_delete_guard_names_the_real_blocker(owner_client, barsy_recorder):
    _wire(barsy_recorder)
    r = await owner_client.delete("/api/catalog/categories/110")     # 2 articles inside
    assert r.status_code == 409
    assert "2" in r.json()["detail"]


@pytest.mark.asyncio
async def test_delete_empty_uses_the_captured_ui_contract(owner_client, barsy_recorder):
    """The contract came from Barsy's OWN UI (Claude-in-Chrome capture — the owner rule): element
    key `id` (NOT cat_id), parent_id as string, weight, top-level mode "0". Blind probes failed on
    exactly this shape. Success = ABSENCE in getlist, not the call's return (timeout ≠ failure)."""
    _wire(barsy_recorder)
    barsy_recorder.set_return("Categories_Delete", 1)
    barsy_recorder.set_return("Categories_getlist", [{"cat_id": 100}, {"cat_id": 110}])  # 120 gone
    r = (await owner_client.delete("/api/catalog/categories/120")).json()
    assert r["ok"] is True
    body = barsy_recorder.body_for("Categories_Delete")
    assert body == {"cats": [{"id": 120, "parent_id": "100", "weight": 1}], "mode": "0"}


@pytest.mark.asyncio
async def test_delete_unconfirmed_is_a_502_not_a_fake_success(owner_client, barsy_recorder):
    """If the category still shows in getlist after the call, report failure — a 'deleted'
    category that survives the next tree read is worse than an error."""
    _wire(barsy_recorder)
    barsy_recorder.set_return("Categories_Delete", 1)
    barsy_recorder.set_return("Categories_getlist", [{"cat_id": 120}])   # still there
    r = await owner_client.delete("/api/catalog/categories/120")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_writes_are_manager_gated(staff_client, barsy_recorder):
    _wire(barsy_recorder)
    assert (await staff_client.post("/api/catalog/categories",
                                    json={"parent_id": 100, "name": "x"})).status_code == 403
    assert (await staff_client.patch("/api/catalog/categories/110",
                                     json={"name": "x"})).status_code == 403
    assert "Categories_create" not in barsy_recorder.operations
    assert "Categories_update" not in barsy_recorder.operations


@pytest.mark.asyncio
async def test_write_gate_dry_runs(owner_client, barsy_recorder, monkeypatch):
    _wire(barsy_recorder)
    monkeypatch.setattr(catalog_module, "barsy_write_live", lambda: False)
    r = (await owner_client.post("/api/catalog/categories",
                                 json={"parent_id": 100, "name": "Ново"})).json()
    assert r["gated"] is True
    assert "Categories_create" not in barsy_recorder.operations


def test_children_are_unwrapped_from_the_barsy_envelope():
    """The regression that mattered. Barsy wraps every node; reading fields off the wrapper gave
    cat_id=None and slug="" for all 14,606 categories, and the empty defaults made it look like
    ordinary sparse data instead of a structural mismatch."""
    root = catalog_module._slim_cat_node(
        {**TREE["category"], "categories": TREE["categories"]})
    dom = root["children"][0]
    assert dom["cat_id"] == 100 and dom["slug"] == "hg"
    assert dom["children"][0]["cat_id"] == 110 and dom["children"][0]["slug"] == "hg-11"
