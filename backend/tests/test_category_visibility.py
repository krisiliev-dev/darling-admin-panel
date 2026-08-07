# -*- coding: utf-8 -*-
"""Category visibility: which categories the public may see.

Panel-side state, because Barsy has no visibility field on a category (schema captured
2026-08-07). The banner went the other way for the opposite reason: Barsy HAS a picture field.
"""
from types import SimpleNamespace

import pytest

from app.routers import catalog as catalog_module
from app.routers import public_categories as pub_module

TOKEN = "tok-for-tests"

# hg (untouched by the owner) > hg-1, hg-2 (both switched on) ; hg-1 > hg-1-1 (untouched)
# REAL Barsy envelope: every node is {"category": {...fields...}, "categories": [...]}.
# The first version of this fixture was flat, and a flat fixture is precisely what let the tree
# slimming ship while returning empty nodes for every real category.
def _n(cat_id, parent_id, name, slug, cnt, children=(), picture=""):
    return {"category": {"cat_id": cat_id, "parent_id": parent_id, "cat_name": name,
                         "slug": slug, "child_articles_cnt": cnt, "cat_picture": picture},
            "categories": list(children)}


# hg (untouched by the owner) > hg-1, hg-2 (both switched on) ; hg-1 > hg-1-1 (untouched)
TREE = {"category": {"cat_id": None, "parent_id": None, "cat_name": "root", "slug": None,
                     "child_articles_cnt": 0},
        "categories": [
            _n(100, None, "dom", "hg", 0, [
                _n(110, 100, "banya", "hg-1", 3, [
                    _n(111, 110, "dulboko", "hg-1-1", 1),
                ], picture="banner.png"),
                _n(120, 100, "dekor", "hg-2", 5),
            ]),
        ]}


@pytest.fixture(autouse=True)
def _fresh_caches():
    catalog_module._TREE_CACHE.update({"t": 0.0, "root": None})
    catalog_module._invalidate_enabled_feed()


@pytest.fixture
def wired(barsy_recorder, monkeypatch):
    barsy_recorder.set_return("Categories_getTree", TREE)
    monkeypatch.setattr(pub_module, "get_settings",
                        lambda: SimpleNamespace(office_lookup_token=TOKEN))
    return barsy_recorder


def _root():
    return catalog_module._slim_cat_node(
        {**TREE["category"], "categories": TREE["categories"]})


# =========================================================== resolution, as a pure function
def test_an_untouched_ancestor_does_not_disable_its_children():
    """THE one. The owner switched on hg-1..hg-21 and never touched their parent hg. If a missing
    ancestor row counted as "disabled", every one of them would resolve to off and the owner's
    stated configuration could not be expressed at all."""
    out = catalog_module._resolve_enabled_slugs(_root(), {"hg-1": True, "hg-2": True})
    assert out == {"hg-1", "hg-2"}


def test_an_explicitly_disabled_ancestor_cascades():
    """The other half of the asymmetry: absence is neutral, an explicit False is not."""
    out = catalog_module._resolve_enabled_slugs(_root(), {"hg": False, "hg-1": True, "hg-2": True})
    assert out == set()


def test_a_category_nobody_touched_is_off():
    assert catalog_module._resolve_enabled_slugs(_root(), {}) == set()


def test_enabling_a_parent_does_not_enable_its_children():
    """Enablement is not inherited downward — only disablement is. A child must be switched on in
    its own right, otherwise switching on hg would publish all 14,606 descendants at once."""
    out = catalog_module._resolve_enabled_slugs(_root(), {"hg": True})
    assert out == {"hg"}


def test_disablement_reaches_grandchildren():
    out = catalog_module._resolve_enabled_slugs(
        _root(), {"hg": False, "hg-1": True, "hg-1-1": True})
    assert out == set()


# ============================================================================ the toggles
@pytest.mark.asyncio
async def test_a_manager_can_switch_a_category_on_and_it_resolves(manager_client, wired):
    r = await manager_client.put("/api/catalog/categories/visibility/hg-1",
                                 json={"enabled": True})
    assert r.status_code == 200, r.text
    body = (await manager_client.get("/api/catalog/categories/visibility")).json()
    assert body["toggles"] == {"hg-1": True}
    assert body["enabled_slugs"] == ["hg-1"] and body["count"] == 1


@pytest.mark.asyncio
async def test_the_view_shows_what_was_clicked_AND_what_it_resolves_to(manager_client, wired):
    """They disagree exactly when a parent is off, and showing only the clicks is how an operator
    ends up insisting a category is on while the site disagrees."""
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    await manager_client.put("/api/catalog/categories/visibility/hg", json={"enabled": False})
    body = (await manager_client.get("/api/catalog/categories/visibility")).json()
    assert body["toggles"] == {"hg-1": True, "hg": False}
    assert body["enabled_slugs"] == []          # clicked on, resolves to off


@pytest.mark.asyncio
async def test_clearing_returns_to_neutral_and_unblocks_descendants(manager_client, wired):
    """Without DELETE the third state is unreachable: an ancestor switched off could never go back
    to "not my business" without being switched ON, which means something else entirely."""
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    await manager_client.put("/api/catalog/categories/visibility/hg", json={"enabled": False})
    assert (await manager_client.get(
        "/api/catalog/categories/visibility")).json()["enabled_slugs"] == []
    await manager_client.delete("/api/catalog/categories/visibility/hg")
    body = (await manager_client.get("/api/catalog/categories/visibility")).json()
    assert body["enabled_slugs"] == ["hg-1"]
    assert "hg" not in body["toggles"]


@pytest.mark.asyncio
async def test_staff_may_not_change_visibility(staff_client, wired):
    r = await staff_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_tree_carries_the_enabled_set_without_changing_its_nodes(manager_client, wired):
    """The set rides ALONGSIDE the tree. Writing resolved flags into the node dicts would stamp
    one request's answer into a cache shared for ten minutes."""
    await manager_client.put("/api/catalog/categories/visibility/hg-2", json={"enabled": True})
    body = (await manager_client.get("/api/catalog/categories/tree")).json()
    assert body["enabled_slugs"] == ["hg-2"]
    node = body["tree"]["children"][0]
    assert set(node.keys()) == {"cat_id", "parent_id", "name", "slug",
                                "child_articles_cnt", "cat_picture", "children"}


# ====================================================================== the storefront feed
@pytest.mark.asyncio
async def test_the_feed_needs_the_token(client, wired):
    assert (await client.get("/api/public/categories/enabled")).status_code == 401
    assert (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": "nope"})).status_code == 401


@pytest.mark.asyncio
async def test_the_feed_is_off_until_the_owner_sets_a_secret(client, barsy_recorder, monkeypatch):
    barsy_recorder.set_return("Categories_getTree", TREE)
    monkeypatch.setattr(pub_module, "get_settings",
                        lambda: SimpleNamespace(office_lookup_token=""))
    r = await client.get("/api/public/categories/enabled", headers={"X-Office-Token": "x"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_feed_serves_ids_and_slugs_for_the_same_categories(manager_client, client, wired):
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert body["slugs"] == ["hg-1"] and body["ids"] == [110]
    assert body["count"] == len(body["ids"])     # the storefront asked; they cannot disagree


@pytest.mark.asyncio
async def test_nothing_enabled_is_an_authoritative_empty_200(client, wired):
    """An empty list here means the owner has genuinely enabled nothing, and the storefront must
    show nothing rather than drop its filter."""
    r = await client.get("/api/public/categories/enabled", headers={"X-Office-Token": TOKEN})
    assert r.status_code == 200
    assert r.json()["ids"] == [] and r.json()["count"] == 0


@pytest.mark.asyncio
async def test_a_resolution_FAILURE_is_503_and_never_an_empty_200(client, wired, monkeypatch):
    """THE contract. If failure came back as {"ids": []} the storefront could not tell it from the
    test above, and would have to assume the worse of the two every time Barsy hiccuped."""
    def boom():
        raise RuntimeError("Barsy unreachable")

    monkeypatch.setattr(catalog_module, "_tree_root", lambda *a, **k: boom())
    r = await client.get("/api/public/categories/enabled", headers={"X-Office-Token": TOKEN})
    assert r.status_code == 503
    assert "ids" not in r.text


@pytest.mark.asyncio
async def test_a_toggle_takes_effect_immediately_not_after_the_ttl(manager_client, client, wired):
    """Flip and read in ONE test — the RESULT, not the fact that an invalidation was called. A
    disable that takes two minutes to land is a product still selling from a category the owner
    just switched off."""
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    first = (await client.get("/api/public/categories/enabled",
                              headers={"X-Office-Token": TOKEN})).json()
    assert first["ids"] == [110]

    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": False})
    second = (await client.get("/api/public/categories/enabled",
                               headers={"X-Office-Token": TOKEN})).json()
    assert second["ids"] == []                   # not the cached [110]


@pytest.mark.asyncio
async def test_a_toggle_for_a_category_barsy_no_longer_has_is_reported_not_silent(
        manager_client, client, wired):
    """A category the owner switched ON that Barsy no longer has. It cannot appear in ids, and
    the first version of this reported it from the resolved set — where it can never be, because
    resolution walks the live tree. The field was permanently empty and looked like good news."""
    await manager_client.put("/api/catalog/categories/visibility/hg-99", json={"enabled": True})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert body["ids"] == [] and body["orphaned_slugs"] == ["hg-99"]


@pytest.mark.asyncio
async def test_a_disabled_toggle_for_a_missing_category_is_not_reported(manager_client, client,
                                                                        wired):
    """Only ON toggles orphan. A category the owner switched OFF and that Barsy then removed is
    doing exactly what was asked of it, and reporting it would be noise that trains people to
    ignore the list."""
    await manager_client.put("/api/catalog/categories/visibility/hg-98", json={"enabled": False})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert body["orphaned_slugs"] == []
# ============================================================== the banner map (Files_getList)
def _files(monkeypatch, table, calls=None):
    """Fake Files_getList: {cat_id: [records]}. Records mimic the real shape.

    WRAPS the recorder rather than replacing it — everything except Files_getList must still
    reach barsy_recorder, above all Categories_getTree, without which _tree_root fails and the
    feed correctly answers 503 for reasons that have nothing to do with banners.
    """
    prev = catalog_module.barsy.call

    def _call(method, body=None, **kw):
        if method != "Files_getList":
            return prev(method, body, **kw)
        cid = (body or {}).get("obj_id")
        if calls is not None:
            calls.append(cid)
        v = table.get(cid)
        if isinstance(v, Exception):
            raise v
        return v or []
    monkeypatch.setattr(catalog_module.barsy, "call", _call)


def _rec(name, url):
    return {"name": name, "size": 1, "type": "image/png", "last_modified": None,
            "url": url, "thumbnail_url": url + "&thumb=1",
            "delete_url": url + "&del=1", "delete_cache_url": "", "content": None, "file": None}


@pytest.fixture(autouse=True)
def _fresh_banner_cache():
    catalog_module._invalidate_banner_map()


@pytest.mark.asyncio
async def test_the_banner_url_comes_from_the_file_matching_cat_picture(manager_client, wired,
                                                                      monkeypatch, client):
    _files(monkeypatch, {110: [_rec("other.png", "/x/other"), _rec("banner.png", "/x/banner")]})
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert body["banners"]["hg-1"]["url"] == "/x/banner"          # matched by NAME, not position
    assert body["banners"]["hg-1"]["thumbnail_url"] == "/x/banner&thumb=1"
    assert body["banners"]["hg-1"]["orphaned_main"] is False


@pytest.mark.asyncio
async def test_an_empty_cat_picture_falls_back_to_the_first_file_and_says_so(manager_client,
                                                                            wired, monkeypatch,
                                                                            client):
    """The orphaned-main case: deleting a category's main picture clears cat_picture without
    promoting a replacement, so the collection outlives the pointer. Barsy's own UI shows the
    first file; reporting "no banner" would be wrong."""
    _files(monkeypatch, {120: [_rec("left-behind.png", "/x/left")]})
    await manager_client.put("/api/catalog/categories/visibility/hg-2", json={"enabled": True})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert body["banners"]["hg-2"]["url"] == "/x/left"
    assert body["banners"]["hg-2"]["orphaned_main"] is True        # so the panel can nudge


@pytest.mark.asyncio
async def test_a_category_with_no_files_simply_has_no_banner(manager_client, wired, monkeypatch,
                                                             client):
    _files(monkeypatch, {110: []})
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    body = (await client.get("/api/public/categories/enabled",
                             headers={"X-Office-Token": TOKEN})).json()
    assert "hg-1" not in body["banners"]        # absent, so the storefront serves its default
    assert body["ids"] == [110]                  # and the category is still ENABLED


@pytest.mark.asyncio
async def test_only_enabled_categories_are_looked_up(manager_client, wired, monkeypatch, client):
    """One Barsy round trip per category. Doing this for the whole 14,606-node tree would be
    indefensible, and nothing would fail if it happened — it would just be slow forever."""
    calls: list = []
    _files(monkeypatch, {110: [_rec("banner.png", "/x/b")]}, calls=calls)
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    await client.get("/api/public/categories/enabled", headers={"X-Office-Token": TOKEN})
    assert calls == [110]                        # not hg, not hg-2, not hg-1-1


@pytest.mark.asyncio
async def test_a_banner_failure_does_NOT_become_a_503(manager_client, wired, monkeypatch, client):
    """THE one. The enabled ids fail CLOSED because an empty list is authoritative; banners fail
    OPEN because a missing picture costs a default image. Collapsing the two policies would let a
    Barsy hiccup over decoration hide the entire catalogue."""
    _files(monkeypatch, {110: RuntimeError("Barsy said no")})
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    r = await client.get("/api/public/categories/enabled", headers={"X-Office-Token": TOKEN})
    assert r.status_code == 200                  # NOT 503
    body = r.json()
    assert body["ids"] == [110]                  # the gate is unaffected
    assert body["banners"] == {}                 # only the decoration is missing


@pytest.mark.asyncio
async def test_switching_a_category_off_drops_its_banner(manager_client, wired, monkeypatch,
                                                         client):
    """The banner map is keyed on the enabled set, so a visibility write must invalidate it —
    otherwise a category that just went dark keeps serving its picture."""
    _files(monkeypatch, {110: [_rec("banner.png", "/x/b")]})
    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": True})
    first = (await client.get("/api/public/categories/enabled",
                              headers={"X-Office-Token": TOKEN})).json()
    assert first["banners"]["hg-1"]["url"] == "/x/b"

    await manager_client.put("/api/catalog/categories/visibility/hg-1", json={"enabled": False})
    second = (await client.get("/api/public/categories/enabled",
                               headers={"X-Office-Token": TOKEN})).json()
    assert second["ids"] == [] and second["banners"] == {}
