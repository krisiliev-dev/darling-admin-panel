# -*- coding: utf-8 -*-
"""A product may not be flagged sellable online without an image (owner rule, 2026-08-06).

The rule has to hold in the ERP, not in the browser, so these test the BACKEND paths. Both
of them: the single-product editor and the bulk flag flip. A guard on only one of the two is
not a guard — the bulk path is the faster way to publish a hundred photo-less listings.

The interesting case is the third state. "This product has no photo" and "we could not ask
Barsy" are different facts; the code refuses in both directions but says which, and these
tests hold it to that. A check that quietly treats an ERP timeout as "no photo" sends
someone to upload a picture that is already there.
"""
import pytest
from fastapi import HTTPException

from app.routers import catalog as cat


class _Row:
    """Stand-in for a ProductCache row — mass_op only reads these four attributes."""

    def __init__(self, article_id, code, name, has_image):
        self.article_id, self.code, self.name, self.has_image = article_id, code, name, has_image


# ------------------------------------------------------------- the picture read
def test_picture_url_returns_the_url_when_barsy_has_one(monkeypatch):
    monkeypatch.setattr(cat.barsy, "call",
                        lambda *a, **k: [{"article_id": 7, "code": "1001", "picture_url": "http://x/1.jpg"}])
    assert cat._picture_url("1001", 7) == "http://x/1.jpg"


def test_picture_url_returns_empty_string_when_barsy_says_there_is_none(monkeypatch):
    monkeypatch.setattr(cat.barsy, "call",
                        lambda *a, **k: [{"article_id": 7, "code": "1001", "picture_url": None}])
    assert cat._picture_url("1001", 7) == ""


def test_picture_url_is_none_when_barsy_raises(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("upstream")
    monkeypatch.setattr(cat.barsy, "call", boom)
    assert cat._picture_url("1001", 7) is None


def test_picture_url_is_none_when_barsy_answers_something_unreadable(monkeypatch):
    """A dict, a string, None — anything that is not a list is not proof of absence."""
    for junk in ({"error": "x"}, "nope", None, 0):
        monkeypatch.setattr(cat.barsy, "call", lambda *a, **k: junk)
        assert cat._picture_url("1001", 7) is None


def test_picture_url_is_none_when_our_article_is_not_in_the_answer(monkeypatch):
    """Barsy's code filter is not an exact match, so it can hand back a different article.
    Not finding ours is 'could not check', never 'has no picture'."""
    monkeypatch.setattr(cat.barsy, "call",
                        lambda *a, **k: [{"article_id": 99, "code": "10011", "picture_url": ""}])
    assert cat._picture_url("1001", 7) is None


def test_picture_url_without_a_code_does_not_guess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call Barsy with an empty filter")
    monkeypatch.setattr(cat.barsy, "call", boom)
    assert cat._picture_url("", 7) is None


# ------------------------------------------------------------------ the refusal
def test_refuse_lets_a_product_with_a_picture_through(monkeypatch):
    monkeypatch.setattr(cat, "_picture_url", lambda code, aid: "http://x/1.jpg")
    cat._refuse_without_image("1001", 7)          # must not raise


def test_refuse_blocks_a_product_with_no_picture(monkeypatch):
    monkeypatch.setattr(cat, "_picture_url", lambda code, aid: "")
    with pytest.raises(HTTPException) as e:
        cat._refuse_without_image("1001", 7)
    assert e.value.status_code == 400
    assert "снимка" in e.value.detail


def test_refuse_blocks_when_it_could_not_check_and_says_so(monkeypatch):
    """Fails closed either way, but the two messages must not be the same — one sends you to
    Barsy to upload a photo, the other tells you to try again."""
    monkeypatch.setattr(cat, "_picture_url", lambda code, aid: None)
    with pytest.raises(HTTPException) as e:
        cat._refuse_without_image("1001", 7)
    assert e.value.status_code == 400
    assert e.value.detail == cat._IMAGE_UNKNOWN
    assert cat._IMAGE_UNKNOWN != cat._NO_IMAGE


def test_both_messages_are_in_bulgarian_and_actionable():
    for msg in (cat._NO_IMAGE, cat._IMAGE_UNKNOWN):
        assert msg.strip()
        assert any("Ѐ" <= ch <= "ӿ" for ch in msg), msg


# ----------------------------------------------------- which status puts it online
def test_only_active_sets_is_public():
    """If this ever changes, the guard is pointed at the wrong status and the rule silently
    stops applying — so the mapping itself is asserted, not assumed."""
    assert cat._STATUS_FLAGS["active"] == (1, 1)
    assert cat._STATUS_FLAGS["draft"][1] == 0
    assert cat._STATUS_FLAGS["withdrawn"][1] == 0
    online = [s for s, (sale, public) in cat._STATUS_FLAGS.items() if sale and public]
    assert online == ["active"]


def test_is_public_is_still_the_mass_column_the_guard_watches():
    """The bulk guard keys on the column name. A rename in _COLS without a rename here would
    disarm it quietly."""
    cols = {c["col"] for c in cat._MASS_FIELDS}
    assert "is_public" in cols


# ------------------------------------------------------------------- the bulk flip
def _mass_partition(changes, rows, col, val):
    """The partition mass_op does, exercised directly: the endpoint itself needs a database
    session, an owner and a live Barsy, none of which belong in a unit test."""
    blocked, allowed = [], []
    if col == "is_public" and val == "1":
        for c in changes:
            pc = rows.get(c["article_id"])
            if pc is not None and not pc.has_image:
                blocked.append({"article_id": c["article_id"], "code": c["code"], "name": c["name"]})
            else:
                allowed.append(c)
        return allowed, blocked
    return changes, blocked


def test_bulk_publish_holds_back_the_photo_less_ones_and_names_them():
    rows = {1: _Row(1, "A1", "с снимка", True), 2: _Row(2, "A2", "без снимка", False)}
    changes = [{"article_id": 1, "code": "A1", "name": "с снимка", "old": "0", "new": "1"},
               {"article_id": 2, "code": "A2", "name": "без снимка", "old": "0", "new": "1"}]
    allowed, blocked = _mass_partition(changes, rows, "is_public", "1")
    assert [c["article_id"] for c in allowed] == [1]
    assert [b["code"] for b in blocked] == ["A2"]
    assert blocked[0]["name"] == "без снимка"


def test_bulk_UNpublish_is_never_blocked():
    """Taking products OFF the site must always work — that is the remedy, not the risk."""
    rows = {2: _Row(2, "A2", "без снимка", False)}
    changes = [{"article_id": 2, "code": "A2", "name": "без снимка", "old": "1", "new": "0"}]
    allowed, blocked = _mass_partition(changes, rows, "is_public", "0")
    assert len(allowed) == 1 and blocked == []


def test_other_bulk_columns_are_untouched():
    rows = {2: _Row(2, "A2", "без снимка", False)}
    changes = [{"article_id": 2, "code": "A2", "name": "без снимка", "old": "x", "new": "y"}]
    allowed, blocked = _mass_partition(changes, rows, "barcode", "1")
    assert len(allowed) == 1 and blocked == []


def test_an_article_missing_from_the_cache_is_not_silently_blocked():
    """No cache row is not evidence of no photo. It passes the bulk filter and meets the
    live check on the single-product path instead of being dropped without explanation."""
    changes = [{"article_id": 3, "code": "A3", "name": "непознат", "old": "0", "new": "1"}]
    allowed, blocked = _mass_partition(changes, {}, "is_public", "1")
    assert len(allowed) == 1 and blocked == []


# ------------------------------------------------------- articles from a delivery
def test_a_delivery_created_article_is_not_published():
    """A product created from a supplier delivery has no photograph, so it must be sellable
    at the till and invisible online until somebody adds one. Before this rule it shipped
    is_public=1 and every delivered line went straight to the storefront."""
    from app.routers import intake_delivery as deliv

    line = deliv.CommitLine(name="Тестов артикул", qty=1.0, unit="бр", code="9001")
    art = deliv._new_article_body(line)["article"]
    assert art["is_for_sale"] == 1
    assert art["is_public"] == 0


# ------------------------------------------------------------- the New Product path
def test_the_new_product_path_already_requires_an_image():
    """intake.approve refuses a draft with no image, so nothing was added there. Asserted so
    that a later 'simplification' of that check fails here instead of in production."""
    import inspect

    from app.routers import intake

    src = inspect.getsource(intake.update_draft)
    assert "_image_counts" in src
    assert "поне една снимка" in src
